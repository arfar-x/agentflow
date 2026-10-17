-- What the admin console's database role (`kb_admin`) may do (FR-ADM-04).
--
-- Read everything, so the console can show it; write only the console's own
-- tables. It cannot touch the catalog -- entries, kept text, overrides,
-- checkpoints -- because the scheduler is the one component that writes those,
-- with the one set of source credentials (spec §6.11).
--
-- Idempotent, and applied after the migrations rather than inside one: the
-- console is opt-in, so its role may be created long after the tables exist.
-- scripts/kb-init.sh runs this when KB_ADMIN_DB_PASSWORD is set.
GRANT USAGE ON SCHEMA public TO kb_admin;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO kb_admin;

GRANT INSERT, UPDATE, DELETE ON form_record, kb_source TO kb_admin;
-- History is append-only: no UPDATE, no DELETE.
GRANT INSERT ON kb_source_revision TO kb_admin;
-- A request is made by the console and answered by the scheduler; the console
-- may only cancel one, so only the columns a cancel changes.
GRANT INSERT ON sync_request TO kb_admin;
GRANT UPDATE (status, finished_at, error) ON sync_request TO kb_admin;
GRANT USAGE, SELECT ON SEQUENCE sync_request_id_seq, kb_source_revision_id_seq TO kb_admin;
