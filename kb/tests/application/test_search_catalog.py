from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kb.application.use_cases.search_catalog import SearchCatalog
from kb.domain.entry import Entry, EntryType, Location
from kb.domain.merge import Override
from tests.application.fakes import FakeClock, FakeEntryStore

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

REFUNDS = Entry(
    id="confluence-eng:1",
    type=EntryType.DOC,
    title={"en": "Payment reconciliation", "fa": "تطبیق پرداخت"},
    summary={"en": "How refund retries are handled.", "fa": "بازپرداخت"},
    keywords=("refund", "retry", "بازپرداخت"),
    tags=("payments",),
    location=Location(kind="confluence", ref={"page_id": "1"}, url="https://wiki/1"),
    source_id="confluence-eng",
    last_seen_at=NOW - timedelta(hours=1),
)
ONBOARDING = Entry(
    id="confluence-eng:2",
    type=EntryType.DOC,
    title={"en": "Onboarding"},
    keywords=("onboarding",),
    tags=("people",),
    location=Location(kind="confluence", ref={"page_id": "2"}, url="https://wiki/2"),
    source_id="confluence-eng",
    last_seen_at=NOW - timedelta(hours=1),
)
GLOSSARY = Entry(
    id="confluence-eng:3",
    type=EntryType.TERM,
    title={"en": "Refund"},
    keywords=("refund",),
    location=Location(kind="confluence", ref={"page_id": "3"}, url="https://wiki/3"),
    source_id="confluence-eng",
    last_seen_at=NOW - timedelta(hours=1),
)


@pytest.fixture()
def search():
    store = FakeEntryStore()
    for entry in (REFUNDS, ONBOARDING, GLOSSARY):
        store.upsert(entry)
    return store, SearchCatalog(store, FakeClock(NOW))


def test_a_question_finds_a_document_whose_title_uses_other_words(search):
    # Covers: AS-01, FR-SRCH-01
    # The reason the catalog exists: "refund retries" must reach a page called
    # "Payment reconciliation", which keyword search over the wiki cannot do.
    _, catalog = search
    result = catalog.execute(["how do we handle refund retries?"])
    assert result.hits[0].entry.id == REFUNDS.id


def test_a_query_in_one_language_finds_a_document_summarized_in_both(search):
    # Covers: AS-02, FR-TXT-03
    _, catalog = search
    result = catalog.execute(["بازپرداخت"])
    assert [hit.entry.id for hit in result.hits] == [REFUNDS.id]


def test_both_phrasings_of_one_question_are_fused(search):
    # Covers: FR-SRCH-01
    _, catalog = search
    result = catalog.execute(["refund retries", "بازپرداخت"])
    assert result.hits[0].entry.id == REFUNDS.id
    assert result.queries == ("refund retries", "بازپرداخت")


def test_results_carry_the_next_call_to_make(search):
    # Covers: FR-ENT-09
    _, catalog = search
    hit = catalog.execute(["refund"]).hits[0]
    assert hit.fetch_hint == {"tool": "confluence_get_page", "args": {"page_id": hit.entry.location.ref["page_id"]}}


def test_type_and_tag_filters_narrow_the_search(search):
    # Covers: FR-SRCH-03
    _, catalog = search
    assert [h.entry.id for h in catalog.execute(["refund"], types=[EntryType.TERM]).hits] == [GLOSSARY.id]
    assert [h.entry.id for h in catalog.execute(["refund"], tags=["payments"]).hits] == [REFUNDS.id]


def test_limit_is_respected(search):
    # Covers: FR-SRCH-03
    _, catalog = search
    assert len(catalog.execute(["refund"], limit=1).hits) == 1


def test_excluded_entries_never_appear_and_do_not_consume_a_slot(search):
    # Covers: FR-SRCH-04, FR-OVR-04
    store, catalog = search
    store.set_override(Override(entry_id=REFUNDS.id, exclude=True, note="duplicate"))
    ids = [hit.entry.id for hit in catalog.execute(["refund"], limit=1).hits]
    assert ids == [GLOSSARY.id]


def test_overridden_text_is_what_the_agent_sees(search):
    # Covers: FR-SRCH-06
    store, catalog = search
    store.set_override(Override(entry_id=REFUNDS.id, summary={"en": "Corrected by a human."}))
    hit = catalog.execute(["refund retries"]).hits[0]
    assert hit.entry.summary["en"] == "Corrected by a human."
    assert "بازپرداخت" in hit.entry.summary["fa"]


def test_soft_deleted_entries_drop_out_of_search(search):
    # Covers: FR-SRCH-04
    store, catalog = search
    store.mark_missing([REFUNDS.id], at=NOW)
    assert REFUNDS.id not in [hit.entry.id for hit in catalog.execute(["refund"]).hits]


def test_a_stale_entry_is_flagged_not_hidden(search):
    # Covers: FR-SRCH-05
    store, catalog = search
    store.upsert(REFUNDS.model_copy(update={"last_seen_at": NOW - timedelta(days=5)}))
    hit = catalog.execute(["refund retries"]).hits[0]
    # Still returned: the agent reads the live document anyway, and a stale
    # pointer beats no pointer.
    assert hit.entry.id == REFUNDS.id
    assert hit.stale is True


def test_a_search_that_finds_nothing_is_logged_as_a_gap(search):
    # Covers: FR-SRCH-07, AS-03
    # Half of the scenario: the query is recorded, so what nobody wrote down
    # becomes a ranked list. The other half -- the agent saying so instead of
    # guessing -- is the instruction asserted in tests/test_deployment.py.
    store, catalog = search
    result = catalog.execute(["quarterly hiring plan"])
    assert result.hits == ()
    assert store.misses and "quarterly hiring plan" in store.misses[0][0]


def test_an_empty_or_punctuation_only_query_is_not_logged_as_a_gap(search):
    # Covers: FR-SRCH-07
    store, catalog = search
    result = catalog.execute(["", "   ", "???"])
    assert result.hits == ()
    assert result.notes == ("no searchable terms in the query",)
    assert store.misses == []


def test_duplicate_queries_are_searched_once(search):
    # Covers: FR-SRCH-08
    store, catalog = search
    searched: list[str] = []
    original = store.search

    def spy(query, **kwargs):
        searched.append(query)
        return original(query, **kwargs)

    store.search = spy
    catalog.execute(["Refund", "refund", "refund!"])
    assert searched == ["refund"]


def test_long_queries_are_truncated_before_reaching_the_store(search):
    # Covers: FR-SRCH-08
    store, catalog = search
    seen: list[str] = []
    original = store.search

    def spy(query, **kwargs):
        seen.append(query)
        return original(query, **kwargs)

    store.search = spy
    catalog.execute(["refund " * 500])
    assert len(seen[0]) <= 500
