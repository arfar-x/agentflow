"""Stored raw content against a real Postgres: the SQL paging, the conditional
write, and the deletes that the fakes can only imitate."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kb.domain.content import prepare
from kb.domain.entry import Entry, EntryType, Location

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def adr(entry_id: str, *, source_id: str = "gitlab-adrs", version: str = "v1") -> Entry:
    return Entry(
        id=entry_id,
        type=EntryType.SPEC,
        title={"en": f"ADR {entry_id}"},
        location=Location(kind="gitlab", ref={"path": f"docs/{entry_id}.md"}, url=f"https://gitlab/{entry_id}"),
        source_id=source_id,
        source_version=version,
        last_seen_at=NOW,
    )


def test_text_round_trips_and_is_read_a_slice_at_a_time(store):
    # Covers: FR-CNT-06
    store.upsert(adr("a"))
    body = "مقدمه " + "x" * 50 + " پایان"
    assert store.put_content("a", prepare(body, 1024), source_version="v1") is True

    first = store.read_content("a", offset=0, limit=10)
    rest = store.read_content("a", offset=10, limit=10_000)
    assert first.text + rest.text == body, "character offsets, as Python counts them"
    assert first.total_chars == len(body)
    assert first.source_version == "v1"
    assert first.original_bytes == len(body.encode("utf-8"))
    assert store.read_content("a", offset=len(body) + 5, limit=10).text == ""
    assert store.read_content("nobody", offset=0, limit=10) is None


def test_storing_identical_text_again_writes_nothing(store):
    store.upsert(adr("a"))
    content = prepare("same text", 1024)
    assert store.put_content("a", content, source_version="v1") is True
    assert store.put_content("a", content, source_version="v1") is False
    assert store.put_content("a", content, source_version="v2") is True
    assert store.read_content("a", offset=0, limit=100).source_version == "v2"


def test_text_goes_with_a_missing_document_and_with_a_removed_entry(store):
    # Covers: FR-CNT-06
    store.upsert(adr("a"))
    store.upsert(adr("b"))
    store.put_content("a", prepare("a text", 1024), source_version="v1")
    store.put_content("b", prepare("b text", 1024), source_version="v1")

    store.mark_missing(["a"], at=NOW + timedelta(days=1))
    assert store.read_content("a", offset=0, limit=10) is None

    with store._connection.transaction(), store._connection.cursor() as cursor:
        cursor.execute("DELETE FROM entry WHERE id = 'b'")
    assert store.read_content("b", offset=0, limit=10) is None


def test_purging_a_source_leaves_other_sources_alone(store):
    store.upsert(adr("a"))
    store.upsert(adr("other", source_id="gitlab-specs"))
    store.put_content("a", prepare("a", 1024), source_version="v1")
    store.put_content("other", prepare("o", 1024), source_version="v1")

    assert store.purge_content("gitlab-adrs") == 1
    assert store.content_ids(["a", "other"]) == {"other"}
    assert store.purge_content("gitlab-adrs") == 0


def test_status_reports_what_keeping_text_costs(store):
    store.upsert(adr("a"))
    store.put_content("a", prepare("y" * 3000, 1024), source_version="v1")
    raw = store.status()["raw_content"]
    assert raw["documents"] == 1 and raw["truncated"] == 1
    assert raw["text_bytes"] == 1024
    assert raw["disk_bytes"] > 0


def test_the_query_role_can_read_text_and_never_write_it(store):
    # Covers: FR-CNT-10
    with store._connection.transaction(), store._connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'kb_reader'")
        if cursor.fetchone() is None:
            cursor.execute("CREATE ROLE kb_reader NOLOGIN")
        # The grant lives in the migration, so re-apply it now the role exists.
        cursor.execute("DELETE FROM schema_migration WHERE name = '003_entry_content.sql'")
    store.migrate()

    with store._connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT has_table_privilege('kb_reader', 'entry_content', 'SELECT') AS read,
                   has_table_privilege('kb_reader', 'entry_content', 'INSERT') AS insert,
                   has_table_privilege('kb_reader', 'entry_content', 'UPDATE') AS update,
                   has_table_privilege('kb_reader', 'entry_content', 'DELETE') AS delete
            """
        )
        rights = cursor.fetchone()
    assert rights["read"]
    assert not (rights["insert"] or rights["update"] or rights["delete"])
