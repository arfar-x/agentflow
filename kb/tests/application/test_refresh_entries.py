"""Draining the refresh queue.

The cheapest freshness in the design: nothing happens until somebody opens a
stale entry, and then only that one document is read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kb.application.ports.knowledge_source import SourceDocument
from kb.application.use_cases.get_entry import GetEntry
from kb.application.use_cases.reconcile_document import ReconcileDocument
from kb.application.use_cases.refresh_entries import RefreshEntries
from kb.domain.entry import EntryType, Location
from tests.application.fakes import (
    FakeClock,
    FakeEntryStore,
    FakeKnowledgeSource,
    FakeSummarizer,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def document(external_id: str = "1", body: str = "Body.") -> SourceDocument:
    return SourceDocument(
        source_id="confluence-eng",
        external_id=external_id,
        title="A page",
        body=body,
        location=Location(kind="confluence", ref={"page_id": external_id}, url=f"https://wiki/{external_id}"),
        type=EntryType.DOC,
        version="1",
    )


@pytest.fixture()
def wired():
    store, summarizer, clock = FakeEntryStore(), FakeSummarizer(), FakeClock(NOW)
    reconcile = ReconcileDocument(store, summarizer, clock)
    reconcile.execute(document())  # something in the catalog to refresh
    source = FakeKnowledgeSource("confluence-eng", [document()])
    refresher = RefreshEntries(store, reconcile, lambda sid: source if sid == "confluence-eng" else None, clock)
    return store, summarizer, clock, source, refresher


def test_reading_a_stale_entry_asks_for_a_refresh(wired):
    # Covers: FR-REC-10
    store, _, clock, _, _ = wired
    clock.advance(timedelta(days=2))  # now older than stale_after

    view = GetEntry(store, clock).execute("confluence-eng:1")

    assert view.stale is True
    assert "confluence-eng:1" in store.refresh_queue


def test_reading_a_fresh_entry_asks_for_nothing(wired):
    # Covers: FR-REC-10
    store, _, clock, _, _ = wired
    assert GetEntry(store, clock).execute("confluence-eng:1").stale is False
    assert store.refresh_queue == {}


def test_a_failure_to_queue_never_fails_the_read(wired):
    # The caller wanted the entry, not the housekeeping. The MCP server's role
    # may also simply lack the grant.
    store, _, clock, _, _ = wired
    clock.advance(timedelta(days=2))

    def explode(entry_id, *, at):
        raise PermissionError("kb_reader may not insert here")

    store.queue_refresh = explode
    assert GetEntry(store, clock).execute("confluence-eng:1").stale is True


def test_draining_re_reads_only_the_queued_document(wired):
    # Covers: FR-REC-10
    store, summarizer, clock, source, refresher = wired
    store.queue_refresh("confluence-eng:1", at=NOW)

    report = refresher.execute()

    assert (report.taken, report.unchanged) == (1, 1)
    assert store.refresh_queue == {}, "done means out of the queue"
    assert len(summarizer.calls) == 1, "unchanged content: still no second model call"


def test_a_changed_document_is_re_summarized_by_the_drain(wired):
    store, summarizer, clock, _, _ = wired
    changed = FakeKnowledgeSource("confluence-eng", [document(body="Rewritten upstream.")])
    refresher = RefreshEntries(
        store,
        ReconcileDocument(store, summarizer, clock),
        lambda sid: changed,
        clock,
    )
    store.queue_refresh("confluence-eng:1", at=NOW)

    report = refresher.execute()

    assert report.refreshed == 1
    assert "Rewritten" in store.entries["confluence-eng:1"].summary["en"]


def test_an_empty_queue_costs_nothing(wired):
    _, summarizer, _, _, refresher = wired
    report = refresher.execute()
    assert report == type(report)()
    assert len(summarizer.calls) == 1  # only the fixture's


def test_a_document_gone_from_its_source_is_left_for_the_nightly_pass(wired):
    # Covers: FR-REC-05
    # One failed read is not evidence of deletion -- only a full pass, which
    # sees everything the source has, may conclude that.
    store, _, clock, _, _ = wired
    empty = FakeKnowledgeSource("confluence-eng", [])
    refresher = RefreshEntries(store, ReconcileDocument(store, FakeSummarizer(), clock), lambda s: empty, clock)
    store.queue_refresh("confluence-eng:1", at=NOW)

    report = refresher.execute()

    assert report.missing_at_source == 1
    assert store.entries["confluence-eng:1"].deleted_at is None


def test_a_source_that_is_down_leaves_the_entry_with_its_error(wired):
    # Covers: FR-SCH-03
    store, _, clock, _, _ = wired

    class Broken:
        source_id = "confluence-eng"

        def fetch(self, external_id):
            raise OSError("connection refused")

    refresher = RefreshEntries(
        store, ReconcileDocument(store, FakeSummarizer(), clock), lambda s: Broken(), clock
    )
    store.queue_refresh("confluence-eng:1", at=NOW)

    report = refresher.execute()

    assert report.failed and "connection refused" in report.failed[0]
    assert store.refresh_queue["confluence-eng:1"]["last_error"]


def test_an_entry_queued_for_a_source_that_is_gone_is_reported_not_retried_forever(wired):
    store, _, clock, _, _ = wired
    refresher = RefreshEntries(
        store, ReconcileDocument(store, FakeSummarizer(), clock), lambda s: None, clock
    )
    store.queue_refresh("confluence-eng:1", at=NOW)

    report = refresher.execute()

    assert report.failed and "not configured" in report.failed[0]


def test_an_entry_deleted_since_it_was_queued_is_dropped(wired):
    store, _, _, _, refresher = wired
    store.queue_refresh("confluence-eng:gone", at=NOW)

    report = refresher.execute()

    assert report.missing_at_source == 1
    assert store.refresh_queue == {}


def test_repeated_failures_eventually_stop_being_retried(wired):
    # Otherwise one unreadable document is retried on every drain, forever.
    store, _, _, _, _ = wired
    store.queue_refresh("confluence-eng:1", at=NOW)

    for _ in range(5):
        assert store.take_refresh_batch(limit=10) == ["confluence-eng:1"]
    assert store.take_refresh_batch(limit=10) == []
    assert store.refresh_queue["confluence-eng:1"]["attempts"] == 5, "the row stays, to be looked at"
