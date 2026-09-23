"""The Postgres adapter, against a real Postgres.

The fakes prove the use cases; these prove the thing that actually stores and
ranks. Every test here is one the fakes structurally cannot catch: SQL, the
trigram index, JSON round-tripping through JSONB, and what survives a re-sync.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kb.domain.entry import Entry, EntryType, Location
from kb.domain.merge import Override
from kb.domain.text import normalize

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

REFUNDS = Entry(
    id="confluence-eng:1",
    type=EntryType.DOC,
    title={"en": "Payment reconciliation", "fa": "تطبیق پرداخت"},
    summary={
        "en": "How refund retries are handled: three attempts with backoff.",
        "fa": "روش انجام بازپرداخت و تلاش مجدد",
    },
    keywords=("refund", "retry", "بازپرداخت"),
    tags=("payments",),
    owner_team="payments",
    location=Location(kind="confluence", ref={"page_id": "1"}, url="https://wiki/1"),
    source_id="confluence-eng",
    content_hash="hash-1",
    source_version="42",
    created_at=NOW - timedelta(days=10),
    updated_at=NOW - timedelta(days=10),
    last_seen_at=NOW - timedelta(hours=1),
)
ONBOARDING = Entry(
    id="confluence-eng:2",
    type=EntryType.DOC,
    title={"en": "Onboarding checklist"},
    summary={"en": "Mentions a refund only in passing."},
    keywords=("onboarding",),
    tags=("people",),
    location=Location(kind="confluence", ref={"page_id": "2"}, url="https://wiki/2"),
    source_id="confluence-eng",
    last_seen_at=NOW,
)
GLOSSARY = Entry(
    id="gitlab-platform:3",
    type=EntryType.TERM,
    title={"en": "Refund"},
    keywords=("refund",),
    location=Location(kind="gitlab", ref={"path": "docs/glossary.md"}, url="https://gitlab/3"),
    source_id="gitlab-platform",
    last_seen_at=NOW,
)


@pytest.fixture()
def catalog(store):
    for entry in (REFUNDS, ONBOARDING, GLOSSARY):
        store.upsert(entry)
    return store


def test_the_schema_applies_once_and_re_runs_clean(store):
    # Covers: NFR-DEP-06
    assert store.migrate() == []  # the fixture already applied it


def test_an_entry_survives_the_round_trip_through_jsonb(catalog):
    # Covers: FR-ENT-07
    # Every field, including the Persian text and the location ref, must come
    # back identical -- the store and the model share one schema, so a lossy
    # write would corrupt entries rather than fail.
    assert catalog.get(REFUNDS.id) == REFUNDS
    assert catalog.get_many([REFUNDS.id, GLOSSARY.id]) == {REFUNDS.id: REFUNDS, GLOSSARY.id: GLOSSARY}
    assert catalog.get("nothing-like-this") is None


def test_a_question_reaches_a_page_whose_title_uses_other_words(catalog):
    # Covers: AS-01
    # The founding case: "refund retries" must find "Payment reconciliation",
    # which is exactly what wiki keyword search cannot do.
    assert catalog.search(normalize("how do we handle refund retries?"))[0] == REFUNDS.id


def test_a_persian_query_finds_a_page_summarized_in_persian(catalog):
    # Covers: AS-02
    assert REFUNDS.id in catalog.search(normalize("بازپرداخت"))


def test_persian_spelling_variants_match_because_both_sides_are_normalized(catalog):
    # Covers: FR-TXT-03
    # Same word, Arabic yeh/kaf instead of the Persian letters: it must still
    # match, which only works because the stored text went through the same
    # normalizer.
    arabic_spelling = "بازپرداخت".replace("ی", "ي")
    assert REFUNDS.id in catalog.search(normalize(arabic_spelling))


def test_a_term_in_the_title_outranks_the_same_term_in_a_summary(catalog):
    # Covers: FR-SRCH-10
    ranked = catalog.search(normalize("refund"))
    assert ranked.index(GLOSSARY.id) < ranked.index(ONBOARDING.id)


def test_results_are_filtered_by_type_and_tag_and_limited(catalog):
    # Covers: FR-SRCH-03
    assert catalog.search(normalize("refund"), types=[EntryType.TERM]) == [GLOSSARY.id]
    assert catalog.search(normalize("refund"), tags=["payments"]) == [REFUNDS.id]
    assert len(catalog.search(normalize("refund"), limit=1)) == 1


def test_search_ordering_is_stable_across_identical_runs(catalog):
    # Covers: FR-SRCH-02
    first = catalog.search(normalize("refund"))
    assert all(catalog.search(normalize("refund")) == first for _ in range(3))


def test_an_excluded_entry_disappears_from_search_but_keeps_its_row(catalog):
    # Covers: FR-SRCH-04, FR-OVR-04
    catalog.set_override(Override(entry_id=REFUNDS.id, exclude=True, note="duplicate of 3"))
    assert REFUNDS.id not in catalog.search(normalize("refund retries"))
    assert catalog.get(REFUNDS.id) == REFUNDS


def test_an_override_survives_the_next_sync_of_its_source(catalog):
    # Covers: FR-OVR-06
    # The correction an operator typed must outlive sync overwriting the entry,
    # or nobody will bother making corrections twice.
    catalog.set_override(
        Override(entry_id=REFUNDS.id, summary={"en": "Corrected: retries stop after three attempts."})
    )
    resynced = REFUNDS.model_copy(update={"content_hash": "hash-2", "updated_at": NOW})
    catalog.upsert(resynced)  # exactly what reconciliation does on a changed page

    assert catalog.get_override(REFUNDS.id).summary == {
        "en": "Corrected: retries stop after three attempts."
    }
    assert REFUNDS.id in catalog.search(normalize("corrected retries"))


def test_an_override_recorded_before_its_entry_waits_for_it(store):
    # Covers: FR-OVR-06
    # An operator may correct something the catalog has not synced yet; that
    # must not fail, and must apply once the entry arrives.
    store.set_override(Override(entry_id=REFUNDS.id, tags=("billing",)))
    assert store.search(normalize("refund")) == []

    store.upsert(REFUNDS)
    assert store.search(normalize("refund retries"), tags=["billing"]) == [REFUNDS.id]


def test_clearing_an_override_restores_the_generated_text(catalog):
    # Covers: FR-OVR-06
    catalog.set_override(Override(entry_id=REFUNDS.id, exclude=True))
    assert REFUNDS.id not in catalog.search(normalize("refund retries"))

    catalog.clear_override(REFUNDS.id)
    assert REFUNDS.id in catalog.search(normalize("refund retries"))


def test_touching_an_entry_moves_last_seen_only(catalog):
    # Covers: FR-REC-03
    later = NOW + timedelta(hours=2)
    catalog.touch(REFUNDS.id, at=later, source_version="43")

    entry = catalog.get(REFUNDS.id)
    assert entry.last_seen_at == later
    assert entry.source_version == "43"
    assert entry.updated_at == REFUNDS.updated_at  # unchanged content is not a change
    assert entry.created_at == REFUNDS.created_at


def test_a_missing_document_leaves_search_and_comes_back_on_revival(catalog):
    # Covers: FR-REC-05
    assert catalog.mark_missing([REFUNDS.id], at=NOW) == 1
    assert REFUNDS.id not in catalog.search(normalize("refund retries"))
    assert catalog.get(REFUNDS.id).deleted_at == NOW
    assert catalog.mark_missing([REFUNDS.id], at=NOW) == 0  # already gone: not double-counted

    catalog.revive(REFUNDS.id)
    assert catalog.get(REFUNDS.id).deleted_at is None
    assert REFUNDS.id in catalog.search(normalize("refund retries"))


def test_reconciliation_can_ask_one_source_for_everything_it_owns(catalog):
    # Covers: FR-ENT-04
    assert catalog.ids_for_source("confluence-eng") == {REFUNDS.id, ONBOARDING.id}
    assert catalog.ids_for_source("gitlab-platform") == {GLOSSARY.id}

    catalog.mark_missing([ONBOARDING.id], at=NOW)
    assert catalog.ids_for_source("confluence-eng") == {REFUNDS.id}
    assert catalog.ids_for_source("confluence-eng", include_deleted=True) == {REFUNDS.id, ONBOARDING.id}


def test_gaps_are_recorded(catalog):
    # Covers: FR-SRCH-07
    catalog.log_miss("quarterly hiring plan", at=NOW)
    with catalog._connection.cursor() as cursor:
        cursor.execute("SELECT query, at FROM search_miss")
        rows = cursor.fetchall()
    assert [(row["query"], row["at"]) for row in rows] == [("quarterly hiring plan", NOW)]


def test_checkpoints_round_trip(catalog):
    assert catalog.get_checkpoint("confluence-eng") is None
    catalog.set_checkpoint("confluence-eng", "2026-09-24T00:00:00Z")
    catalog.set_checkpoint("confluence-eng", "2026-09-24T12:00:00Z")
    assert catalog.get_checkpoint("confluence-eng") == "2026-09-24T12:00:00Z"


def test_the_search_index_is_rebuildable_from_the_entries(catalog):
    # Covers: NFR-DEP-06
    # Losing the derived index must be a rebuild, not a restore -- this is what
    # lets backups cover only the gap log and the checkpoints.
    with catalog._connection.transaction(), catalog._connection.cursor() as cursor:
        cursor.execute("TRUNCATE entry_search")
    assert catalog.search(normalize("refund retries")) == []

    assert catalog.rebuild_search_index() == 3
    assert catalog.search(normalize("refund retries"))[0] == REFUNDS.id


def test_the_query_role_can_read_and_log_gaps_and_nothing_else(store):
    # Covers: NFR-DEP-05
    # The MCP server runs as this role, so the blast radius of anything the
    # model can reach is bounded by the grants, not by our SQL being careful.
    with store._connection.transaction(), store._connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'kb_reader'")
        if cursor.fetchone() is None:
            cursor.execute("CREATE ROLE kb_reader NOLOGIN")
        cursor.execute(
            "SELECT name FROM schema_migration WHERE name = '001_initial.sql'"
        )  # grants live in the migration, so re-apply them now the role exists
        cursor.execute("DELETE FROM schema_migration WHERE name = '001_initial.sql'")
    store.migrate()

    with store._connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT has_table_privilege('kb_reader', 'entry', 'SELECT')   AS read_entry,
                   has_table_privilege('kb_reader', 'entry', 'UPDATE')   AS write_entry,
                   has_table_privilege('kb_reader', 'entry', 'DELETE')   AS delete_entry,
                   has_table_privilege('kb_reader', 'search_miss', 'INSERT') AS log_gap,
                   has_table_privilege('kb_reader', 'entry_override', 'INSERT') AS write_override
            """
        )
        rights = cursor.fetchone()
    assert rights["read_entry"] and rights["log_gap"]
    assert not rights["write_entry"] and not rights["delete_entry"] and not rights["write_override"]
