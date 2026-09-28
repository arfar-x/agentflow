"""Syncing one source, against fakes.

The properties worth protecting are about *cost* and *safety*: a steady-state
run must be free, a full pass must notice deletions, an incremental one must
never mistake "not mentioned" for "deleted", and one malformed document must
not take the run down with it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kb.application.ports.knowledge_source import SourceDocument
from kb.application.use_cases.reconcile_document import ReconcileDocument
from kb.application.use_cases.sync_source import Mode, SyncSource
from kb.domain.entry import EntryType, Location
from tests.application.fakes import (
    FakeClock,
    FakeEntryStore,
    FakeKnowledgeSource,
    FakeSummarizer,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def document(external_id: str, *, title: str = "A page", body: str = "Body.") -> SourceDocument:
    return SourceDocument(
        source_id="confluence-eng",
        external_id=external_id,
        title=title,
        body=body,
        location=Location(kind="confluence", ref={"page_id": external_id}, url=f"https://wiki/{external_id}"),
        type=EntryType.DOC,
        version="1",
    )


@pytest.fixture()
def wired():
    store, summarizer, clock = FakeEntryStore(), FakeSummarizer(), FakeClock(NOW)
    reconcile = ReconcileDocument(store, summarizer, clock)
    return store, summarizer, clock, SyncSource(store, reconcile, clock)


def test_a_first_run_catalogs_everything_the_source_has(wired):
    # Covers: FR-REC-09
    store, summarizer, _, sync = wired
    source = FakeKnowledgeSource("confluence-eng", [document("1"), document("2")])

    report = sync.execute(source, mode=Mode.FULL)

    assert (report.created, report.updated, report.unchanged) == (2, 0, 0)
    assert report.summarized == 2
    assert len(store.entries) == 2


def test_a_second_run_over_unchanged_content_is_free(wired):
    # Covers: FR-REC-02, NFR-PRF-03
    # The claim the whole cadence rests on: a nightly full pass over a
    # steady-state source makes no model calls and writes nothing.
    store, summarizer, _, sync = wired
    source = FakeKnowledgeSource("confluence-eng", [document("1"), document("2")])
    sync.execute(source, mode=Mode.FULL)
    writes = store.upserts

    report = sync.execute(source, mode=Mode.FULL)

    assert report.unchanged == 2 and report.created == 0
    assert report.summarized == 0
    assert len(summarizer.calls) == 2, "still only the two from the first run"
    assert store.upserts == writes


def test_a_full_pass_soft_deletes_what_the_source_no_longer_lists(wired):
    # Covers: FR-REC-09, FR-REC-05
    store, _, _, sync = wired
    sync.execute(FakeKnowledgeSource("confluence-eng", [document("1"), document("2")]), mode=Mode.FULL)

    report = sync.execute(FakeKnowledgeSource("confluence-eng", [document("1")]), mode=Mode.FULL)

    assert report.missing == 1
    assert store.entries["confluence-eng:2"].deleted_at == NOW
    assert store.entries["confluence-eng:1"].deleted_at is None


def test_an_incremental_run_never_mistakes_silence_for_deletion(wired):
    # Covers: FR-REC-08
    # An incremental feed only yields what changed, so everything else is
    # absent by design -- deleting on that basis would empty the catalog.
    store, _, _, sync = wired
    sync.execute(FakeKnowledgeSource("confluence-eng", [document("1"), document("2")]), mode=Mode.FULL)

    report = sync.execute(FakeKnowledgeSource("confluence-eng", [document("1")]), mode=Mode.INCREMENTAL)

    assert report.missing == 0
    assert store.entries["confluence-eng:2"].deleted_at is None


def test_an_edit_at_the_source_reaches_the_catalog_with_nobody_touching_it(wired):
    # Covers: AS-04
    store, summarizer, _, sync = wired
    sync.execute(FakeKnowledgeSource("confluence-eng", [document("1", body="Three attempts.")]), mode=Mode.FULL)

    edited = FakeKnowledgeSource("confluence-eng", [document("1", body="Now five attempts.")])
    sync.execute(edited, mode=Mode.INCREMENTAL)

    assert "five" in store.entries["confluence-eng:1"].summary["en"]


def test_a_deletion_at_the_source_leaves_search_and_is_reported(wired):
    # Covers: AS-05
    store, _, _, sync = wired
    sync.execute(FakeKnowledgeSource("confluence-eng", [document("1"), document("2")]), mode=Mode.FULL)

    report = sync.execute(FakeKnowledgeSource("confluence-eng", [document("1")]), mode=Mode.FULL)

    assert report.missing == 1, "reported, so an operator can see what vanished"
    assert store.entries["confluence-eng:2"].deleted_at is not None, "soft-deleted, not erased"
    assert store.search("a page") == ["confluence-eng:1"]


def test_an_operators_correction_outlives_the_next_sync(wired):
    # Covers: AS-06
    from kb.domain.merge import Override

    store, _, _, sync = wired
    source = FakeKnowledgeSource("confluence-eng", [document("1")])
    sync.execute(source, mode=Mode.FULL)
    store.set_override(Override(entry_id="confluence-eng:1", summary={"en": "What it actually says."}))

    sync.execute(FakeKnowledgeSource("confluence-eng", [document("1", body="Rewritten upstream.")]), mode=Mode.FULL)

    assert store.get_override("confluence-eng:1").summary == {"en": "What it actually says."}


def test_a_changed_document_is_resummarized_and_counted(wired):
    store, summarizer, _, sync = wired
    sync.execute(FakeKnowledgeSource("confluence-eng", [document("1")]), mode=Mode.FULL)

    changed = FakeKnowledgeSource("confluence-eng", [document("1", body="Rewritten.")])
    report = sync.execute(changed, mode=Mode.INCREMENTAL)

    assert (report.updated, report.summarized) == (1, 1)


def test_one_malformed_document_is_reported_and_the_run_continues(wired):
    # Covers: FR-REC-06
    store, _, _, sync = wired
    unfetchable = SourceDocument(
        source_id="confluence-eng",
        external_id="broken",
        title="No page id anywhere",
        body="...",
        # A Confluence location with no page_id can never be read back, so
        # Entry refuses it -- exactly the case that must not kill a run over a
        # 900-page space.
        location=Location(kind="confluence", ref={}, url="https://wiki/broken"),
    )
    source = FakeKnowledgeSource("confluence-eng", [document("1"), unfetchable, document("2")])

    report = sync.execute(source, mode=Mode.FULL)

    assert report.created == 2
    assert len(report.rejected) == 1
    assert "broken" in report.rejected[0] and "page_id" in report.rejected[0]
    assert report.missing == 0, "a rejected document is not a deleted one"


def test_a_dry_run_reads_the_source_and_writes_nothing(wired):
    store, summarizer, _, sync = wired
    source = FakeKnowledgeSource("confluence-eng", [document("1"), document("2")])

    report = sync.execute(source, mode=Mode.FULL, dry_run=True)

    assert report.seen == 2
    assert (report.created, report.summarized) == (0, 0)
    assert store.entries == {} and summarizer.calls == []
    assert report.checkpoint is None, "a dry run must not advance the checkpoint"


def test_the_checkpoint_is_reported_only_after_a_real_run(wired):
    # Covers: FR-REC-08
    _, _, _, sync = wired
    source = FakeKnowledgeSource("confluence-eng", [document("1")], checkpoint="2026-09-24 12:00")
    assert sync.execute(source, mode=Mode.INCREMENTAL).checkpoint == "2026-09-24 12:00"


def test_a_revived_document_is_counted_as_such(wired):
    # Covers: FR-REC-05
    store, _, clock, sync = wired
    source = FakeKnowledgeSource("confluence-eng", [document("1")])
    sync.execute(source, mode=Mode.FULL)
    store.mark_missing(["confluence-eng:1"], at=NOW)

    clock.advance(timedelta(hours=1))
    report = sync.execute(source, mode=Mode.FULL)

    assert report.revived == 1
    assert store.entries["confluence-eng:1"].deleted_at is None
