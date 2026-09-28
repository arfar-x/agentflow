-- Lazy refresh: reading a stale entry asks for it to be re-checked.
--
-- A queue rather than a direct refresh, because of who is allowed to do what.
-- The MCP server holds a read-only database role and no source credentials at
-- all -- it could not re-read a wiki page if it wanted to. The scheduler has
-- both. So the reader records "somebody cared about this one", and the
-- scheduler acts on it: the entries people actually read stay freshest, and
-- neither component grows a privilege it should not have.

CREATE TABLE IF NOT EXISTS refresh_queue (
    entry_id     TEXT PRIMARY KEY,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Counted so a document that fails to refresh forever can be spotted
    -- rather than silently retried on every drain.
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT
);

CREATE INDEX IF NOT EXISTS refresh_queue_order_idx ON refresh_queue (requested_at);

-- The query path may append here, the same way it appends to the gap log, and
-- still may not touch anything else.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kb_reader') THEN
        EXECUTE 'GRANT INSERT ON refresh_queue TO kb_reader';
        EXECUTE 'GRANT SELECT ON refresh_queue TO kb_reader';
    END IF;
END
$$;
