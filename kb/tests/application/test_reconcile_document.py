from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kb.application.ports.knowledge_source import SourceDocument
from kb.application.ports.summarizer import SummaryDraft
from kb.application.use_cases.reconcile_document import Action, ReconcileDocument
from kb.domain.entry import EntryType, Location
from tests.application.fakes import FakeClock, FakeEntryStore, FakeSummarizer

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def make_document(**overrides) -> SourceDocument:
    base = dict(
        source_id="confluence-eng",
        external_id="123456",
        title="Refund retry policy",
        body="Retries run three times with backoff.",
        location=Location(kind="confluence", ref={"page_id": "123456"}, url="https://wiki/123456"),
        type=EntryType.DOC,
        version="42",
        updated_at=NOW - timedelta(days=1),
    )
    return SourceDocument(**{**base, **overrides})


@pytest.fixture()
def wired():
    store, summarizer, clock = FakeEntryStore(), FakeSummarizer(), FakeClock(NOW)
    return store, summarizer, clock, ReconcileDocument(store, summarizer, clock)


def test_a_new_document_is_summarized_and_stored(wired):
    # Covers: FR-REC-01, FR-ENT-09
    store, summarizer, _, reconcile = wired
    result = reconcile.execute(make_document())

    assert result.action is Action.CREATED
    assert result.summarized is True
    entry = store.get(result.entry_id)
    assert entry.title["en"] == "Refund retry policy"
    assert entry.source_id == "confluence-eng"
    assert entry.fetch_hint == {"tool": "confluence_get_page", "args": {"page_id": "123456"}}
    assert summarizer.calls == ["123456"]


def test_an_unchanged_document_costs_no_model_call_and_no_write(wired):
    # Covers: FR-REC-02, NFR-PRF-01
    store, summarizer, clock, reconcile = wired
    reconcile.execute(make_document())
    writes_after_first = store.upserts

    clock.advance(timedelta(hours=1))
    result = reconcile.execute(make_document())

    # This is the property the whole cost model rests on.
    assert result.action is Action.UNCHANGED
    assert result.summarized is False
    assert len(summarizer.calls) == 1
    assert store.upserts == writes_after_first
    assert store.touches == 1


def test_touching_an_unchanged_entry_updates_last_seen_but_not_updated_at(wired):
    # Covers: FR-REC-03, FR-ENT-08
    store, _, clock, reconcile = wired
    entry_id = reconcile.execute(make_document()).entry_id
    first = store.get(entry_id)

    clock.advance(timedelta(hours=1))
    reconcile.execute(make_document())
    second = store.get(entry_id)

    assert second.last_seen_at == NOW + timedelta(hours=1)
    assert second.updated_at == first.updated_at


def test_changed_content_is_resummarized_and_keeps_its_original_created_at(wired):
    # Covers: FR-REC-04, FR-ENT-08
    store, summarizer, clock, reconcile = wired
    entry_id = reconcile.execute(make_document()).entry_id
    created_at = store.get(entry_id).created_at

    clock.advance(timedelta(days=2))
    result = reconcile.execute(make_document(body="Retries now run five times.", version="43"))

    assert result.action is Action.UPDATED
    assert result.summarized is True
    assert len(summarizer.calls) == 2
    entry = store.get(entry_id)
    assert entry.created_at == created_at
    assert entry.updated_at == NOW + timedelta(days=2)
    assert entry.source_version == "43"


def test_a_version_bump_with_identical_content_still_counts_as_a_change(wired):
    # Covers: FR-REC-04
    # The version is part of the hash: a source whose body we truncate or
    # whose version we track separately must not look unchanged.
    _, summarizer, _, reconcile = wired
    reconcile.execute(make_document())
    reconcile.execute(make_document(version="43"))
    assert len(summarizer.calls) == 2


def test_a_document_that_comes_back_is_revived_not_duplicated(wired):
    # Covers: FR-REC-05
    store, _, clock, reconcile = wired
    entry_id = reconcile.execute(make_document()).entry_id
    store.mark_missing([entry_id], at=NOW)

    clock.advance(timedelta(hours=1))
    result = reconcile.execute(make_document())

    assert result.action is Action.REVIVED
    assert result.summarized is False  # unchanged content: still no model call
    assert store.get(entry_id).deleted_at is None
    assert len(store.entries) == 1


def test_a_revived_document_whose_content_also_changed_is_resummarized(wired):
    # Covers: FR-REC-05
    store, summarizer, _, reconcile = wired
    entry_id = reconcile.execute(make_document()).entry_id
    store.mark_missing([entry_id], at=NOW)

    result = reconcile.execute(make_document(body="Rewritten.", version="44"))

    assert result.action is Action.REVIVED
    assert result.summarized is True
    assert store.get(entry_id).deleted_at is None


def test_the_same_document_always_gets_the_same_id(wired):
    # Covers: FR-REC-07, FR-ENT-05
    _, _, _, reconcile = wired
    first = reconcile.execute(make_document()).entry_id
    second = reconcile.execute(make_document(title="Renamed", body="Different")).entry_id
    # Renaming a page must not fork it into a second entry.
    assert first == second


def test_a_summarizer_that_returns_no_title_still_produces_a_findable_entry(wired):
    # Covers: FR-REC-06
    store, _, _, reconcile = wired
    reconcile._summarizer = FakeSummarizer({"123456": SummaryDraft(title={})})

    entry_id = reconcile.execute(make_document()).entry_id

    # Degraded, not lost: the source title is kept so the document is still
    # findable and readable when summarization fails.
    assert store.get(entry_id).title == {"und": "Refund retry policy"}


def test_documents_from_different_sources_never_collide(wired):
    # Covers: FR-ENT-05
    _, _, _, reconcile = wired
    a = reconcile.execute(make_document(source_id="confluence-eng")).entry_id
    b = reconcile.execute(make_document(source_id="confluence-biz")).entry_id
    assert a != b
