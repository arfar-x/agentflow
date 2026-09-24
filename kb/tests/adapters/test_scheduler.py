"""The scheduler loop, driven one tick at a time.

`schedule.due()` already proves the cadence rules, so these are about what the
loop does with them: that it records runs, that one broken source doesn't take
down the rest, and that it survives a config file being edited underneath it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kb.adapters.inbound.scheduler import Scheduler
from kb.application.use_cases.reconcile_document import ReconcileDocument
from kb.application.use_cases.schedule import JobKind
from kb.application.use_cases.sync_source import SyncSource
from kb.application.ports.knowledge_source import SourceDocument
from kb.domain.entry import EntryType, Location
from tests.application.fakes import (
    FakeClock,
    FakeEntryStore,
    FakeKnowledgeSource,
    FakeSummarizer,
)

NOON = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

CONFIG = """
approved: true
defaults:
  mechanisms:
    incremental:  { enabled: true, every: 15m }
    full_scrape:  { enabled: false }
    lazy_refresh: { enabled: false }
sources:
  - id: confluence-eng
    kind: confluence
    enabled: true
    spaces: [ENG]
"""


def document(external_id: str = "1") -> SourceDocument:
    return SourceDocument(
        source_id="confluence-eng",
        external_id=external_id,
        title="A page",
        body="Body.",
        location=Location(kind="confluence", ref={"page_id": external_id}, url="https://wiki/1"),
        type=EntryType.DOC,
        version="1",
    )


class StubContainer:
    def __init__(self, store, clock, source=None, fail_with: Exception | None = None):
        self.store = store
        self.clock = clock
        self.reconcile = ReconcileDocument(store, FakeSummarizer(), clock)
        self.sync = SyncSource(store, self.reconcile, clock)
        self.source = source or FakeKnowledgeSource("confluence-eng", [document()])
        self.fail_with = fail_with


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    store, clock = FakeEntryStore(), FakeClock(NOON)
    container = StubContainer(store, clock)
    path = tmp_path / "kb-sources.yaml"
    path.write_text(CONFIG, encoding="utf-8")

    def build_source(config, env=None):
        if container.fail_with:
            raise container.fail_with
        return container.source

    monkeypatch.setattr("kb.adapters.outbound.source_factory.build_source", build_source)
    return container, store, Scheduler(container, sources_file=path), path


def test_a_tick_runs_what_is_due_and_nothing_else(wired):
    # Covers: FR-SCH-01
    container, store, scheduler, _ = wired

    jobs = scheduler.tick(NOON)

    assert [job.kind for job in jobs] == [JobKind.INCREMENTAL]
    assert len(store.entries) == 1


def test_a_job_is_not_repeated_until_its_cadence_comes_round(wired):
    # Covers: FR-SCH-01
    _, _, scheduler, _ = wired
    scheduler.tick(NOON)

    assert scheduler.tick(NOON + timedelta(minutes=5)) == []
    assert scheduler.tick(NOON + timedelta(minutes=15)) != []


def test_every_run_is_recorded_with_its_counts(wired):
    # Covers: FR-SCH-04
    # Logs rotate; "was this working last week?" has to be answerable after.
    _, store, scheduler, _ = wired

    scheduler.tick(NOON)

    assert len(store.runs) == 1
    run = store.runs[0]
    assert run["source_id"] == "confluence-eng"
    assert run["mechanism"] == "incremental"
    assert run["counts"]["created"] == 1
    assert run["error"] is None
    assert run["finished_at"] >= run["started_at"]


def test_a_source_that_fails_is_recorded_and_does_not_stop_the_scheduler(wired):
    # Covers: FR-SCH-03
    container, store, scheduler, _ = wired
    container.fail_with = RuntimeError("confluence is down")

    jobs = scheduler.tick(NOON)

    assert jobs, "the job still ran -- it just failed"
    assert store.runs[0]["error"] == "confluence is down"
    # And the next cadence is attempted rather than the loop giving up.
    container.fail_with = None
    assert scheduler.tick(NOON + timedelta(minutes=15)) != []
    assert store.runs[1]["error"] is None


def test_a_failing_source_is_not_retried_every_tick(wired):
    # Covers: FR-SCH-03
    # Retrying a broken source every ten seconds hammers it and buries every
    # other job's log line.
    container, store, scheduler, _ = wired
    container.fail_with = RuntimeError("down")

    scheduler.tick(NOON)
    scheduler.tick(NOON + timedelta(seconds=10))

    assert len(store.runs) == 1


def test_an_unreadable_config_skips_the_tick_instead_of_crashing(wired):
    # The normal case for this is a file being edited right now.
    _, store, scheduler, path = wired
    path.write_text("approved: true\nsources: [\n", encoding="utf-8")

    assert scheduler.tick(NOON) == []
    assert store.runs == []


def test_a_missing_config_file_is_survivable(tmp_path):
    store, clock = FakeEntryStore(), FakeClock(NOON)
    scheduler = Scheduler(StubContainer(store, clock), sources_file=tmp_path / "absent.yaml")
    assert scheduler.tick(NOON) == []


def test_a_config_with_syncing_switched_off_schedules_nothing(wired):
    # Covers: FR-CFG-05
    _, store, scheduler, path = wired
    path.write_text(CONFIG.replace("approved: true", "approved: false"), encoding="utf-8")

    assert scheduler.tick(NOON) == []
    assert store.entries == {}


def test_the_checkpoint_advances_only_after_a_run_returns(wired):
    # Covers: FR-REC-08
    container, store, scheduler, _ = wired
    container.source = FakeKnowledgeSource("confluence-eng", [document()], checkpoint="2026-09-24 12:00")

    scheduler.tick(NOON)

    assert store.checkpoints["confluence-eng"] == "2026-09-24 12:00"


def test_a_failed_run_leaves_the_checkpoint_where_it_was(wired):
    # Covers: FR-REC-08
    # Advancing past a failure would skip whatever that run never saw.
    container, store, scheduler, _ = wired
    store.set_checkpoint("confluence-eng", "2026-09-24 11:00")
    container.fail_with = RuntimeError("down")

    scheduler.tick(NOON)

    assert store.checkpoints["confluence-eng"] == "2026-09-24 11:00"


def test_recording_a_run_failing_does_not_take_down_the_scheduler(wired):
    # Losing a run's history is bad; losing the scheduler over it is worse.
    _, store, scheduler, _ = wired

    def explode(**kwargs):
        raise RuntimeError("history table is gone")

    store.record_run = explode
    assert scheduler.tick(NOON) != []
