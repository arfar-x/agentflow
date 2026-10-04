"""Whole-source behavior of kept text: revoking it, and documents that go away."""

from __future__ import annotations

from datetime import datetime, timezone

from kb.application.ports.knowledge_source import SourceDocument
from kb.application.use_cases.reconcile_document import ReconcileDocument
from kb.application.use_cases.refresh_entries import RefreshEntries
from kb.application.use_cases.sync_source import Mode, SyncSource
from kb.domain.content import ContentPolicy
from kb.domain.entry import EntryType, Location
from kb.domain.identity import entry_id_for
from tests.application.fakes import FakeClock, FakeEntryStore, FakeKnowledgeSource, FakeSummarizer

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
KEEP = ContentPolicy(store=True)


def doc(name: str, source_id: str = "gitlab-adrs") -> SourceDocument:
    return SourceDocument(
        source_id=source_id,
        external_id=f"specs:docs/{name}.md",
        title=name,
        body=f"The full text of {name}.",
        location=Location(kind="gitlab", ref={"path": f"docs/{name}.md"}, url=f"https://gitlab/{name}"),
        type=EntryType.SPEC,
        version="v1",
    )


def wire():
    store, clock = FakeEntryStore(), FakeClock(NOW)
    reconcile = ReconcileDocument(store, FakeSummarizer(), clock)
    return store, reconcile, SyncSource(store, reconcile, clock)


def test_an_opted_in_sync_stores_text_for_every_document_it_sees():
    # Covers: FR-CNT-02
    store, _, sync = wire()
    report = sync.execute(FakeKnowledgeSource("gitlab-adrs", [doc("a"), doc("b")]), content=KEEP)
    assert report.content_stored == 2
    assert len(store.contents) == 2


def test_turning_the_flag_off_purges_the_source_on_its_next_sync_of_any_mode():
    # Covers: FR-CNT-05
    store, _, sync = wire()
    sync.execute(FakeKnowledgeSource("gitlab-adrs", [doc("a"), doc("b")]), content=KEEP)
    sync.execute(FakeKnowledgeSource("gitlab-specs", [doc("c", "gitlab-specs")]), content=KEEP)

    report = sync.execute(FakeKnowledgeSource("gitlab-adrs", []), mode=Mode.INCREMENTAL)
    assert report.content_purged == 2
    assert {store.entries[i].source_id for i in store.contents} == {"gitlab-specs"}, (
        "only the source that turned it off"
    )


def test_a_dry_run_never_purges():
    # Covers: FR-CNT-05
    store, _, sync = wire()
    sync.execute(FakeKnowledgeSource("gitlab-adrs", [doc("a")]), content=KEEP)
    report = sync.execute(FakeKnowledgeSource("gitlab-adrs", [doc("a")]), dry_run=True)
    assert report.content_purged == 0 and len(store.contents) == 1


def test_a_document_the_source_removed_takes_its_text_with_it():
    # Covers: FR-CNT-06
    store, _, sync = wire()
    sync.execute(FakeKnowledgeSource("gitlab-adrs", [doc("a"), doc("b")]), content=KEEP)
    report = sync.execute(FakeKnowledgeSource("gitlab-adrs", [doc("a")]), mode=Mode.FULL, content=KEEP)

    assert report.missing == 1
    assert set(store.contents) == {entry_id_for("gitlab-adrs", "specs:docs/a.md")}


def test_a_lazy_refresh_keeps_text_the_way_a_sync_would():
    # Covers: FR-CNT-02
    store, reconcile, _ = wire()
    reconcile.execute(doc("a"))  # catalogued before the source opted in
    entry_id = entry_id_for("gitlab-adrs", "specs:docs/a.md")
    store.queue_refresh(entry_id, at=NOW)

    refresh = RefreshEntries(
        store,
        reconcile,
        lambda _source_id: FakeKnowledgeSource("gitlab-adrs", [doc("a")]),
        FakeClock(NOW),
        resolve_content=lambda source_id: KEEP if source_id == "gitlab-adrs" else ContentPolicy.off(),
    )
    refresh.execute()
    assert entry_id in store.contents
