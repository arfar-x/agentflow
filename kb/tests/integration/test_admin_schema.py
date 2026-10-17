"""The admin console's tables and the role it connects as.

The role is the console's real boundary (FR-ADM-04): whatever the web code does
or fails to do, the database refuses a write to the catalog.
"""

from __future__ import annotations

from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

GRANTS = Path(__file__).resolve().parents[2] / "grants" / "admin.sql"
PASSWORD = "kb-admin-test-only"


@pytest.fixture()
def admin(store, dsn):
    """A connection as `kb_admin`, with the grants applied the way kb-init.sh does."""
    from psycopg.conninfo import make_conninfo

    with store._connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'kb_admin'")
        exists = cursor.fetchone() is not None
        verb = "ALTER" if exists else "CREATE"
        cursor.execute(f"{verb} ROLE kb_admin WITH LOGIN PASSWORD '{PASSWORD}'")
        cursor.execute(GRANTS.read_text(encoding="utf-8"))

    connection = psycopg.connect(
        make_conninfo(dsn, user="kb_admin", password=PASSWORD), autocommit=True
    )
    try:
        yield connection
    finally:
        connection.close()


def _refused(connection, sql: str, params: tuple = ()) -> bool:
    try:
        connection.execute(sql, params)
    except psycopg.errors.InsufficientPrivilege:
        return True
    return False


# Covers: FR-ADM-04
def test_the_console_role_cannot_write_the_catalog(admin, store):
    catalog = {
        "entry": "updated_at",
        "entry_override": "updated_at",
        "entry_search": "type",
        "entry_content": "body",
        "source_checkpoint": "cursor",
        "sync_run": "error",
        "scheduler_heartbeat": "approved",
    }
    for table, column in catalog.items():
        assert _refused(admin, f"DELETE FROM {table}"), table
        assert _refused(admin, f"UPDATE {table} SET {column} = {column}"), table
    assert _refused(admin, "INSERT INTO search_miss (query, at) VALUES ('x', now())")
    assert _refused(admin, "INSERT INTO source_checkpoint (source_id) VALUES ('x')")


# Covers: FR-ADM-04
def test_the_console_role_reads_everything_and_writes_its_own_tables(admin, store):
    for table in ("entry", "entry_content", "sync_run", "sync_request", "scheduler_heartbeat", "form_record"):
        admin.execute(f"SELECT count(*) FROM {table}").fetchone()

    admin.execute(
        "INSERT INTO form_record (slug, title, body) VALUES ('a', '{\"en\": \"A\"}', 'text')"
    )
    admin.execute("UPDATE form_record SET body = 'more' WHERE slug = 'a'")
    admin.execute("DELETE FROM form_record WHERE slug = 'a'")

    admin.execute(
        "INSERT INTO kb_source (id, kind, config) VALUES ('s', 'jira', '{}')"
    )
    admin.execute(
        "INSERT INTO kb_source_revision (action, actor, before_text, after_text, after_hash)"
        " VALUES ('create', 'admin', '', 'x', 'h')"
    )
    # History is append-only, even for the role that writes it.
    assert _refused(admin, "UPDATE kb_source_revision SET actor = 'someone'")
    assert _refused(admin, "DELETE FROM kb_source_revision")


# Covers: FR-ADM-04
def test_the_console_may_queue_and_cancel_but_not_rewrite_a_request(admin, store):
    admin.execute("INSERT INTO sync_request (kind, source_id, mode) VALUES ('sync', 's', 'full')")
    admin.execute("UPDATE sync_request SET status = 'cancelled', finished_at = now()")
    # What a request asks for is fixed once it is made.
    assert _refused(admin, "UPDATE sync_request SET source_id = 'other'")
    assert _refused(admin, "DELETE FROM sync_request")


# The schema half of FR-SCH-08; the behavior is claimed in tests/adapters/test_scheduler.py.
def test_a_sync_run_row_can_exist_before_it_has_finished(store):
    with store._connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO sync_run (source_id, mechanism, started_at, status) "
            "VALUES ('s', 'incremental', now(), 'running') RETURNING finished_at, status, triggered_by"
        )
        row = cursor.fetchone()
    assert row["finished_at"] is None
    assert (row["status"], row["triggered_by"]) == ("running", "scheduled")
