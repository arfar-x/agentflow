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
from kb.application.ports.sync_queue import RequestKind, RequestStatus, SyncRequest
from kb.domain.runs import INTERRUPTED, RunStatus, Trigger
from tests.application.fakes import (
    FakeClock,
    FakeEntryStore,
    FakeHeartbeats,
    FakeKnowledgeSource,
    FakeRunLog,
    FakeSummarizer,
    FakeSyncQueue,
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
        self.runs = FakeRunLog()
        self.queue = FakeSyncQueue()
        self.heartbeats = FakeHeartbeats()
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
    store.runs = container.runs.runs  # the same list the scheduler writes to
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
    assert run.source_id == "confluence-eng"
    assert run.mechanism == "incremental"
    assert run.created == 1
    assert run.error is None
    assert run.finished_at >= run.started_at


def test_a_source_that_fails_is_recorded_and_does_not_stop_the_scheduler(wired):
    # Covers: FR-SCH-03
    container, store, scheduler, _ = wired
    container.fail_with = RuntimeError("confluence is down")

    jobs = scheduler.tick(NOON)

    assert jobs, "the job still ran -- it just failed"
    assert store.runs[0].error == "confluence is down"
    # And the next cadence is attempted rather than the loop giving up.
    container.fail_with = None
    assert scheduler.tick(NOON + timedelta(minutes=15)) != []
    assert store.runs[1].error is None


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
    _, store, _, _ = wired

    def explode(*args, **kwargs):
        raise RuntimeError("history table is gone")

    container, _, scheduler, _ = wired
    container.runs.start_run = explode
    container.runs.finish_run = explode
    assert scheduler.tick(NOON) != []
    assert len(store.entries) == 1, "the run itself still happened"


class Gate:
    """A stand-in for the summarizer's check, counting how often it is asked."""

    def __init__(self, ok: bool = True) -> None:
        self.ok, self.calls = ok, 0

    def __call__(self) -> dict:
        self.calls += 1
        return {"ok": True} if self.ok else {"ok": False, "error": "403 Client Error: Forbidden"}


def gated(wired, gate):
    container, store, scheduler, path = wired
    return container, store, Scheduler(container, sources_file=path, check_summarizer=gate)


def test_an_unusable_summarizer_stops_a_run_before_it_catalogues_anything(wired):
    # Covers: FR-SCH-07
    gate = Gate(ok=False)
    _, store, scheduler = gated(wired, gate)

    scheduler.tick(NOON)

    assert store.entries == {}, "nothing was catalogued undescribed"
    assert len(store.runs) == 1
    assert "summarizer" in store.runs[0].error and "403" in store.runs[0].error


def test_a_working_summarizer_lets_the_run_through(wired):
    # Covers: FR-SCH-07
    _, store, scheduler = gated(wired, Gate(ok=True))
    scheduler.tick(NOON)
    assert len(store.entries) == 1 and store.runs[0].error is None


def test_the_next_cadence_tries_again_once_the_summarizer_is_back(wired):
    # Covers: FR-SCH-07
    gate = Gate(ok=False)
    container, store, scheduler = gated(wired, gate)
    scheduler.tick(NOON)

    gate.ok = True
    container.clock.advance(timedelta(minutes=15))  # the scheduler reads this clock
    scheduler.tick(NOON + timedelta(minutes=15))

    assert len(store.entries) == 1
    assert [run.error is None for run in store.runs] == [False, True]


def test_jobs_due_together_share_one_probe(wired):
    # Covers: FR-SCH-07
    container, store, scheduler, path = wired
    path.write_text(CONFIG + """  - id: confluence-ops
    kind: confluence
    enabled: true
    spaces: [OPS]
""", encoding="utf-8")
    gate = Gate()
    Scheduler(container, sources_file=path, check_summarizer=gate).tick(NOON)
    assert len(store.runs) == 2 and gate.calls == 1


def test_without_a_summarizer_configured_nothing_is_gated(wired):
    # The degraded mode (entries under their real titles) is a deliberate,
    # already-reported choice, not a defect to refuse over.
    _, store, scheduler, _ = wired
    scheduler.tick(NOON)
    assert len(store.entries) == 1


# -- run history, recovery and requests (spec §6.4a, §6.11) ---------------------


class Watching(FakeKnowledgeSource):
    """Records what the run history looked like while the source was being read."""

    def __init__(self, container, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_by_history = None
        self._container = container

    def changed_since(self, checkpoint):
        self.seen_by_history = list(self._container.runs.runs)
        return super().changed_since(checkpoint)


def test_a_run_is_visible_while_it_is_still_running(wired):
    # Covers: FR-SCH-08
    container, _, scheduler, _ = wired
    watching = Watching(container, "confluence-eng", [document()])
    container.source = watching

    scheduler.tick(NOON)

    during = watching.seen_by_history
    assert [(run.status, run.finished_at) for run in during] == [(RunStatus.RUNNING, None)]
    assert container.runs.runs[0].status is RunStatus.SUCCESS


def test_a_finished_run_is_judged_success_warning_or_error(wired):
    # Covers: FR-SCH-08
    container, store, scheduler, _ = wired
    scheduler.tick(NOON)
    assert store.runs[-1].status is RunStatus.SUCCESS

    # A page that cannot become an entry is skipped and reported, not fatal.
    bad = SourceDocument(
        source_id="confluence-eng", external_id="9", title="No id", body="b",
        location=Location(kind="confluence", ref={}, url="https://wiki/9"), version="1",
    )
    container.source = FakeKnowledgeSource("confluence-eng", [document(), bad])
    scheduler.tick(NOON + timedelta(minutes=15))
    assert store.runs[-1].status is RunStatus.WARNING
    assert len(store.runs[-1].rejected) == 1 and "9" in store.runs[-1].rejected[0]

    container.fail_with = RuntimeError("down")
    scheduler.tick(NOON + timedelta(minutes=30))
    assert store.runs[-1].status is RunStatus.ERROR


def test_a_run_the_last_scheduler_never_finished_is_closed_when_this_one_starts(wired):
    # Covers: FR-SCH-08
    container, _, scheduler, _ = wired
    container.runs.start_run(source_id="confluence-eng", mechanism="incremental", started_at=NOON)
    container.queue.requests.append(
        SyncRequest(id=1, source_id="confluence-eng", status=RequestStatus.RUNNING)
    )

    scheduler.tick(NOON)

    stale = container.runs.runs[0]
    assert stale.status is RunStatus.ERROR and stale.error == INTERRUPTED
    assert container.queue.requests[0].status is RequestStatus.FAILED


def test_a_run_says_what_started_it_and_what_it_was(wired):
    # Covers: FR-SCH-09
    container, store, scheduler, _ = wired
    scheduler.tick(NOON)
    scheduled = store.runs[0]
    assert (scheduled.triggered_by, scheduled.dry_run, scheduled.forced) == (
        Trigger.SCHEDULED, False, False,
    )
    assert scheduled.seen == 1 and scheduled.checkpoint is None

    container.queue.enqueue(
        SyncRequest(source_id="confluence-eng", mode="full", dry_run=True, forced=True), at=NOON
    )
    scheduler.tick(NOON + timedelta(minutes=1))
    manual = store.runs[-1]
    assert (manual.triggered_by, manual.mode, manual.dry_run, manual.forced) == (
        Trigger.MANUAL, "full", True, True,
    )
    assert manual.mechanism == "full_scrape"


def test_content_counts_and_the_checkpoint_are_recorded(wired):
    # Covers: FR-SCH-09
    container, store, scheduler, _ = wired
    container.source = FakeKnowledgeSource("confluence-eng", [document()], checkpoint="cp-1")
    scheduler.tick(NOON)
    assert store.runs[0].checkpoint == "cp-1"
    assert (store.runs[0].content_stored, store.runs[0].content_purged) == (0, 0)


def test_a_restart_remembers_when_each_cadence_last_ran(wired):
    # Covers: FR-SCH-10
    container, store, _, path = wired
    ran = NOON - timedelta(minutes=5)
    container.runs.start_run(source_id="confluence-eng", mechanism="incremental", started_at=ran)
    container.runs.finish_run(1, finished_at=ran, counts={})
    # A fresh process: its own state, the same history.
    restarted = Scheduler(container, sources_file=path)

    assert restarted.tick(NOON) == [], "ran five minutes ago -- not due"
    assert [job.kind for job in restarted.tick(NOON + timedelta(minutes=10))] == [JobKind.INCREMENTAL]


def test_a_run_someone_asked_for_does_not_stand_in_for_the_cadence(wired):
    # Covers: FR-SCH-10
    container, _, scheduler, path = wired
    container.runs.start_run(
        source_id="confluence-eng", mechanism="incremental", started_at=NOON,
        triggered_by=Trigger.MANUAL,
    )
    container.runs.finish_run(1, finished_at=NOON, counts={})
    assert [job.kind for job in Scheduler(container, sources_file=path).tick(NOON)] == [JobKind.INCREMENTAL]


def test_a_source_another_process_is_syncing_is_left_alone(wired):
    # Covers: FR-SCH-05
    container, _, scheduler, _ = wired
    scheduler.tick(NOON)  # started; recovery has already happened
    container.runs.start_run(
        source_id="confluence-eng", mechanism="full_scrape", started_at=NOON,
        triggered_by=Trigger.CLI,
    )
    before = len(container.runs.runs)

    scheduler.tick(NOON + timedelta(minutes=15))

    assert len(container.runs.runs) == before, "no second run alongside the CLI's"
    # And the cadence was not consumed, so it goes as soon as the CLI is done.
    container.runs.finish_run(before, finished_at=NOON, counts={})
    assert scheduler.tick(NOON + timedelta(minutes=15, seconds=10)) != []


def serve(container, scheduler, **request):
    container.queue.enqueue(SyncRequest(**request), at=NOON)
    scheduler.tick(NOON)
    return container.queue.requests[-1]


def test_a_requested_sync_is_run_by_the_scheduler_and_answered(wired):
    # Covers: FR-SCH-11
    container, store, scheduler, _ = wired
    request = serve(container, scheduler, source_id="confluence-eng", mode="full")

    assert request.status is RequestStatus.DONE and request.error is None
    assert len(request.run_ids) == 1
    run = container.runs.get_run(request.run_ids[0])
    assert run.triggered_by is Trigger.MANUAL and run.mode == "full"
    assert len(store.entries) == 1


def test_a_requested_dry_run_changes_nothing(wired):
    # Covers: FR-SCH-11
    container, store, scheduler, _ = wired
    path_config = scheduler._sources_file
    path_config.write_text(CONFIG.replace("every: 15m", "every: 1d"), encoding="utf-8")
    # Make the scheduled job not due, so only the request runs.
    scheduler._state = scheduler._state.model_copy(
        update={"last_run": {"incremental:confluence-eng": NOON}, "since": NOON}
    )
    scheduler._started = True

    request = serve(container, scheduler, source_id="confluence-eng", mode="incremental", dry_run=True)

    assert request.status is RequestStatus.DONE
    assert store.entries == {}
    assert container.runs.get_run(request.run_ids[0]).dry_run is True


def test_every_enabled_source_can_be_requested_at_once(wired):
    # Covers: FR-SCH-11
    container, _, scheduler, path = wired
    path.write_text(CONFIG + """  - id: confluence-ops
    kind: confluence
    enabled: true
    spaces: [OPS]
  - id: confluence-off
    kind: confluence
    enabled: false
    spaces: [DORMANT]
""", encoding="utf-8")
    container.queue.enqueue(SyncRequest(source_id="*", mode="incremental"), at=NOON)
    scheduler._started = True  # skip the cadence run; this test is about the request

    scheduler._serve_requests(scheduler_config(path), None)

    request = container.queue.requests[0]
    assert request.status is RequestStatus.DONE
    assert sorted(container.runs.get_run(i).source_id for i in request.run_ids) == [
        "confluence-eng", "confluence-ops",
    ]


def scheduler_config(path):
    from kb.sources_config import load

    return load(path)


def test_a_requested_sync_that_fails_says_why(wired):
    # Covers: FR-SCH-11
    container, _, scheduler, _ = wired
    container.fail_with = RuntimeError("confluence is down")
    request = serve(container, scheduler, source_id="confluence-eng", mode="incremental")

    assert request.status is RequestStatus.FAILED
    assert "confluence-eng: confluence is down" in request.error
    assert container.runs.get_run(request.run_ids[0]).status is RunStatus.ERROR


def test_a_request_is_refused_while_syncing_is_switched_off(wired):
    # Covers: FR-SCH-11, FR-CFG-05
    container, store, scheduler, path = wired
    path.write_text(CONFIG.replace("approved: true", "approved: false"), encoding="utf-8")
    request = serve(container, scheduler, source_id="confluence-eng", mode="full")

    assert request.status is RequestStatus.FAILED and "switched off" in request.error
    assert store.entries == {}


def test_a_request_for_an_unknown_source_fails_naming_it(wired):
    # Covers: FR-SCH-11
    container, _, scheduler, _ = wired
    request = serve(container, scheduler, source_id="nope", mode="full")
    assert request.status is RequestStatus.FAILED and "nope" in request.error


def test_a_cancelled_request_is_never_served(wired):
    # Covers: FR-SCH-11
    container, store, scheduler, _ = wired
    request_id = container.queue.enqueue(SyncRequest(source_id="confluence-eng", mode="full"), at=NOON)
    assert container.queue.cancel(request_id, at=NOON) is True

    scheduler._started = True
    scheduler._serve_requests(scheduler_config(scheduler._sources_file), None)

    assert container.queue.requests[0].status is RequestStatus.CANCELLED
    assert all(run.triggered_by is not Trigger.MANUAL for run in container.runs.runs)


def test_a_request_that_has_started_cannot_be_cancelled(wired):
    # Covers: FR-SCH-11
    container, _, scheduler, _ = wired
    request = serve(container, scheduler, source_id="confluence-eng", mode="full")
    assert container.queue.cancel(request.id, at=NOON) is False


def test_stored_text_can_be_purged_on_request(wired):
    # Covers: FR-SCH-11
    container, store, scheduler, _ = wired
    purged = []
    store.purge_content = lambda source_id: purged.append(source_id) or 3

    request = serve(container, scheduler, source_id="confluence-eng", kind=RequestKind.PURGE_CONTENT)

    assert "confluence-eng" in purged and request.status is RequestStatus.DONE
    run = container.runs.get_run(request.run_ids[0])
    assert (run.mechanism, run.content_purged) == ("purge", 3)


def test_the_heartbeat_reports_what_only_the_scheduler_can_see(wired):
    # Covers: FR-SCH-12
    container, _, _, path = wired
    scheduler = Scheduler(
        container, sources_file=path, environ={"KB_CONFLUENCE_PAT": "s3cret-token-value"}
    )
    scheduler.tick(NOON)

    beat = container.heartbeats.last_beat()
    assert beat.beat_at == NOON and beat.approved is True and beat.config_error is None
    assert beat.credentials["confluence-eng"]["KB_CONFLUENCE_PAT"] is True
    assert beat.credentials["confluence-eng"]["KB_CONFLUENCE_PASSWORD"] is False
    # Names and yes/no -- a status page must not become a leak.
    assert "s3cret-token-value" not in beat.model_dump_json()


def test_the_heartbeat_says_where_the_approval_came_from_and_why_config_failed(wired):
    # Covers: FR-SCH-12
    container, _, scheduler, path = wired
    path.write_text(CONFIG.replace("approved: true", "approved: false"), encoding="utf-8")
    scheduler.tick(NOON)
    beat = container.heartbeats.last_beat()
    assert (beat.approved, beat.approved_from) == (False, "file")

    path.write_text("approved: true\nsources: [\n", encoding="utf-8")
    scheduler.tick(NOON + timedelta(seconds=10))
    assert container.heartbeats.last_beat().config_error


def test_the_heartbeat_reports_the_summarizers_last_verdict(wired):
    # Covers: FR-SCH-12
    gate = Gate(ok=False)
    container, _, scheduler = gated(wired, gate)
    scheduler.tick(NOON)
    scheduler.tick(NOON + timedelta(seconds=10))
    beat = container.heartbeats.last_beat()
    assert beat.summarizer == "failing" and "403" in beat.summarizer_error


def test_the_entries_of_a_source_that_is_gone_can_be_retired_on_request(wired):
    # Covers: FR-SCH-11, FR-ADM-16
    container, store, scheduler, path = wired
    scheduler.tick(NOON)  # catalogues one entry from confluence-eng
    assert len(store.entries) == 1
    # The source is deleted from the file; nothing will sync it again.
    path.write_text("approved: true\nsources: []\n", encoding="utf-8")
    container.queue.enqueue(SyncRequest(source_id="confluence-eng", kind=RequestKind.RETIRE_SOURCE), at=NOON)

    scheduler.tick(NOON + timedelta(minutes=1))

    request = container.queue.requests[-1]
    assert request.status is RequestStatus.DONE
    [entry] = store.entries.values()
    assert entry.deleted_at is not None, "soft-deleted, not erased (FR-REC-05)"
    run = container.runs.get_run(request.run_ids[0])
    assert (run.mechanism, run.missing, run.triggered_by) == ("retire", 1, Trigger.MANUAL)
