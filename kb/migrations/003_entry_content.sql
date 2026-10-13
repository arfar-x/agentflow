-- Raw content: the text of documents whose source opts in (spec §6.10).
--
-- Its own table, not a field of `entry.data`: every search hit and every
-- `get` loads `entry.data`, and a 10 MB document there would make every search
-- pay for text it never shows. Here it is only read when somebody asks for it,
-- one page at a time, and Postgres keeps large values compressed out of line
-- (TOAST) without being asked.
--
-- Derived, like the rest of the catalog: a full sync of an opted-in source
-- rebuilds it, so it needs no backup of its own (NFR-DEP-06).

CREATE TABLE IF NOT EXISTS entry_content (
    -- Text never outlives the entry it belongs to (FR-CNT-06).
    entry_id       TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
    -- The version the text was read at. A reader is only given text whose
    -- version matches the entry's, so a summary and a body from two different
    -- versions of a document are never served together (FR-CNT-11).
    source_version TEXT,
    body           TEXT NOT NULL,
    -- Over `raw_content_max_bytes` the start is kept and this says so; the
    -- whole document's size goes beside it (FR-CNT-07).
    truncated      BOOLEAN NOT NULL DEFAULT FALSE,
    original_bytes BIGINT NOT NULL,
    stored_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The query path reads text; it never writes it (FR-CNT-10).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kb_reader') THEN
        EXECUTE 'GRANT SELECT ON entry_content TO kb_reader';
    END IF;
END
$$;
