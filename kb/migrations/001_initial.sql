-- kb: initial schema.
--
-- Two ideas shape it:
--
-- 1. **The entry's shape is owned by pydantic, not by SQL.** `entry.data` holds
--    the validated entry as JSONB; only the columns something filters, sorts or
--    joins on are promoted out of it. That keeps one schema (the model) instead
--    of two that drift, and a new entry field needs no migration.
-- 2. **`entry_search` is derived and disposable.** It is the entry merged with
--    its override, normalized for matching. Anything in it can be rebuilt from
--    `entry` + `entry_override`, which is why a lost index is a resync rather
--    than a restore (NFR-DEP-06).

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS schema_migration (
    name       TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entry (
    id             TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL,
    type           TEXT NOT NULL,
    data           JSONB NOT NULL,
    content_hash   TEXT NOT NULL DEFAULT '',
    source_version TEXT,
    created_at     TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ,
    last_seen_at   TIMESTAMPTZ,
    -- Soft delete: a document can vanish because it was moved, because a
    -- permission changed, or because the source had a bad day. All three come
    -- back (FR-REC-05).
    deleted_at     TIMESTAMPTZ
);

-- Reconciliation's one hot query: "everything this source still owns".
CREATE INDEX IF NOT EXISTS entry_source_live_idx ON entry (source_id) WHERE deleted_at IS NULL;

-- Human corrections. Deliberately NOT a foreign key to `entry`: an override may
-- be recorded before its entry exists, and must outlive a purge-and-resync of
-- the catalog -- surviving every later sync is the whole point (FR-OVR-06).
CREATE TABLE IF NOT EXISTS entry_override (
    entry_id   TEXT PRIMARY KEY,
    data       JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entry_search (
    entry_id      TEXT PRIMARY KEY REFERENCES entry (id) ON DELETE CASCADE,
    type          TEXT NOT NULL,
    tags          TEXT[] NOT NULL DEFAULT '{}',
    -- False for a soft-deleted entry or one an override excludes, so filtering
    -- is one boolean rather than a join plus a rule duplicated in SQL.
    indexable     BOOLEAN NOT NULL DEFAULT TRUE,
    -- Normalized (kb.domain.text) at write time, and queries are normalized the
    -- same way before they get here -- both sides or neither.
    title_norm    TEXT NOT NULL DEFAULT '',
    keywords_norm TEXT NOT NULL DEFAULT '',
    summary_norm  TEXT NOT NULL DEFAULT '',
    -- Everything concatenated: what the trigram index filters on, before the
    -- per-field weighting scores what survives.
    haystack      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS entry_search_haystack_trgm ON entry_search USING gin (haystack gin_trgm_ops);
CREATE INDEX IF NOT EXISTS entry_search_tags_idx ON entry_search USING gin (tags);
CREATE INDEX IF NOT EXISTS entry_search_type_idx ON entry_search (type) WHERE indexable;

-- Where an incremental run resumes from. Opaque to everything but the source
-- adapter that wrote it: a timestamp for a wiki, a commit SHA for a repository.
CREATE TABLE IF NOT EXISTS source_checkpoint (
    source_id  TEXT PRIMARY KEY,
    cursor     TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Searches that found nothing: a ranked list of what the organization has not
-- written down. One of the two tables here that is not rebuildable, so it is
-- also one of the two that needs backing up.
CREATE TABLE IF NOT EXISTS search_miss (
    id    BIGSERIAL PRIMARY KEY,
    query TEXT NOT NULL,
    at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS search_miss_query_idx ON search_miss (query);

CREATE TABLE IF NOT EXISTS sync_run (
    id          BIGSERIAL PRIMARY KEY,
    source_id   TEXT NOT NULL,
    mechanism   TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    created     INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    unchanged   INTEGER NOT NULL DEFAULT 0,
    revived     INTEGER NOT NULL DEFAULT 0,
    missing     INTEGER NOT NULL DEFAULT 0,
    -- How many model calls a run actually made. The claim that an unchanged
    -- sync is free is only worth making if it is visible after the fact.
    summarized  INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS sync_run_source_idx ON sync_run (source_id, started_at DESC);

-- The query path runs as a least-privilege role (NFR-DEP-05): it may read
-- everything and append to the gap log, and nothing else. The role is created by
-- scripts/kb-init.sh; when it does not exist -- a test database, a developer's
-- laptop -- these grants are skipped rather than failing the migration.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kb_reader') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA public TO kb_reader';
        EXECUTE 'GRANT SELECT ON entry, entry_override, entry_search, source_checkpoint TO kb_reader';
        EXECUTE 'GRANT INSERT ON search_miss TO kb_reader';
        EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE search_miss_id_seq TO kb_reader';
    END IF;
END
$$;
