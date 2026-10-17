-- The admin console (spec §6.11).
--
-- Everything here is operational state the console and the scheduler share:
-- what was asked for, what ran, what the sources looked like, and the manual
-- records. None of it is catalog content, so none of it is rebuildable from
-- sources -- `form_record` and `kb_source_revision` are backed up with the
-- volume like the gap log is (NFR-DEP-06).

-- A run is now written when it starts and finished when it ends, so a run in
-- progress is visible and a crash leaves a row that says so (FR-SCH-08).
-- Existing rows were all written at the end of a run, so they are all final.
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS status        TEXT    NOT NULL DEFAULT 'success';
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS triggered_by  TEXT    NOT NULL DEFAULT 'scheduled';
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS mode          TEXT;
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS dry_run       BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS forced        BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS seen          INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS rejected      JSONB   NOT NULL DEFAULT '[]';
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS content_stored INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS content_purged INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_run ADD COLUMN IF NOT EXISTS checkpoint    TEXT;
UPDATE sync_run SET status = 'error' WHERE error IS NOT NULL AND status = 'success';
CREATE INDEX IF NOT EXISTS sync_run_started_idx ON sync_run (started_at DESC);

-- Work the console asks the scheduler to do. The console holds no source
-- credentials and never writes the catalog, so it cannot do these itself; the
-- scheduler drains this queue one request at a time (FR-SCH-11).
CREATE TABLE IF NOT EXISTS sync_request (
    id           BIGSERIAL PRIMARY KEY,
    -- 'sync' | 'purge_content'
    kind         TEXT        NOT NULL,
    -- A source id, or '*' for every enabled source.
    source_id    TEXT        NOT NULL,
    mode         TEXT,
    dry_run      BOOLEAN     NOT NULL DEFAULT FALSE,
    forced       BOOLEAN     NOT NULL DEFAULT FALSE,
    -- queued | running | done | failed | cancelled
    status       TEXT        NOT NULL DEFAULT 'queued',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    requested_by TEXT        NOT NULL DEFAULT 'admin',
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    -- The runs this request produced ('*' makes one per source).
    run_ids      BIGINT[]    NOT NULL DEFAULT '{}',
    error        TEXT
);
CREATE INDEX IF NOT EXISTS sync_request_queue_idx ON sync_request (requested_at) WHERE status = 'queued';

-- One row, rewritten on every scheduler tick: how the console knows the
-- scheduler is alive, and what it can see that the console cannot (whether the
-- credential variables a source names are set). Names and booleans only.
CREATE TABLE IF NOT EXISTS scheduler_heartbeat (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    beat_at           TIMESTAMPTZ NOT NULL,
    approved          BOOLEAN     NOT NULL DEFAULT TRUE,
    approved_from     TEXT        NOT NULL DEFAULT 'default',
    summarizer        TEXT        NOT NULL DEFAULT 'none',   -- none | unknown | ok | failing
    summarizer_error  TEXT,
    credentials       JSONB       NOT NULL DEFAULT '{}',
    config_error      TEXT,
    working_on        TEXT
);

-- The console's copy of the source configuration. The file is what the
-- scheduler reads and always wins (FR-CFG-12); this is for the console to
-- query, and for history.
CREATE TABLE IF NOT EXISTS kb_source (
    id         TEXT PRIMARY KEY,
    kind       TEXT        NOT NULL,
    -- 'yaml' for what the file declared, 'ui' for what the console added.
    origin     TEXT        NOT NULL DEFAULT 'yaml',
    enabled    BOOLEAN     NOT NULL DEFAULT FALSE,
    config     JSONB       NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only. The whole file before and after, so any change can be undone by
-- hand -- a single-file bind mount has no sibling directory for a backup.
CREATE TABLE IF NOT EXISTS kb_source_revision (
    id          BIGSERIAL PRIMARY KEY,
    -- create | update | delete | settings | file (changed outside the console)
    action      TEXT        NOT NULL,
    source_id   TEXT,
    actor       TEXT        NOT NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    before_text TEXT        NOT NULL,
    after_text  TEXT        NOT NULL,
    diff        TEXT        NOT NULL DEFAULT '',
    after_hash  TEXT        NOT NULL
);
CREATE INDEX IF NOT EXISTS kb_source_revision_at_idx ON kb_source_revision (at DESC);

-- Manual records, the `form` source's documents (FR-SRC-07). `revision` is the
-- document's version: it moves on every save, including one that only changes
-- the summary, which is what makes the content-hash gate publish it.
CREATE TABLE IF NOT EXISTS form_record (
    slug        TEXT PRIMARY KEY,
    title       JSONB       NOT NULL,
    body        TEXT        NOT NULL,
    type        TEXT        NOT NULL DEFAULT 'doc',
    tags        TEXT[]      NOT NULL DEFAULT '{}',
    keywords    TEXT[]      NOT NULL DEFAULT '{}',
    aliases     TEXT[]      NOT NULL DEFAULT '{}',
    owner_team  TEXT,
    audience    TEXT[]      NOT NULL DEFAULT '{all}',
    revision    INTEGER     NOT NULL DEFAULT 1,
    -- The operator-approved SummaryDraft, as JSON. Null means "describe it for
    -- me" (a model call) or, with no model, the real title alone (FR-REC-06).
    draft       JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
