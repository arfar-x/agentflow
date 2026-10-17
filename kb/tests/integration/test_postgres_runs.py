"""Run history, the request queue and the heartbeat against a real Postgres."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kb.application.ports.heartbeat import Heartbeat
from kb.application.ports.sync_queue import RequestKind, RequestStatus, SyncRequest
from kb.domain.runs import INTERRUPTED, RunStatus, Trigger

psycopg = pytest.importorskip("psycopg")

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def runs(store):
    from kb.adapters.outbound.postgres_runs import PostgresRunLog

    return PostgresRunLog(store.connection)


@pytest.fixture()
def queue(store):
    from kb.adapters.outbound.postgres_runs import PostgresSyncQueue

    return PostgresSyncQueue(store.connection)


@pytest.fixture()
def heartbeats(store):
    from kb.adapters.outbound.postgres_runs import PostgresHeartbeat

    return PostgresHeartbeat(store.connection)


def start(runs, source="jira", mechanism="incremental", at=T0, **kwargs):
    return runs.start_run(source_id=source, mechanism=mechanism, started_at=at, **kwargs)


# Covers: FR-SCH-08
def test_a_run_exists_from_its_start_and_is_judged_when_it_ends(runs):
    run_id = start(runs)
    assert runs.get_run(run_id).status is RunStatus.RUNNING
    assert runs.running_sources() == {"jira"}

    runs.finish_run(run_id, finished_at=T0 + timedelta(seconds=3), counts={"created": 2, "seen": 2})

    done = runs.get_run(run_id)
    assert done.status is RunStatus.SUCCESS and done.created == 2 and done.duration_ms == 3000
    assert runs.running_sources() == set()


# Covers: FR-SCH-08, FR-SCH-09
def test_rejected_documents_make_a_warning_and_an_error_beats_it(runs):
    warned = start(runs)
    runs.finish_run(warned, finished_at=T0, counts={}, rejected=["jira:A-1: no id"])
    assert runs.get_run(warned).status is RunStatus.WARNING
    assert runs.get_run(warned).rejected == ("jira:A-1: no id",)

    failed = start(runs, at=T0 + timedelta(minutes=1))
    runs.finish_run(failed, finished_at=T0, counts={}, rejected=["x"], error="down")
    assert runs.get_run(failed).status is RunStatus.ERROR


# Covers: FR-SCH-08
def test_runs_left_running_are_closed_as_interrupted(runs):
    run_id = start(runs)
    assert runs.close_interrupted(at=T0 + timedelta(minutes=5)) == 1
    closed = runs.get_run(run_id)
    assert (closed.status, closed.error) == (RunStatus.ERROR, INTERRUPTED)
    assert runs.close_interrupted(at=T0) == 0


# Covers: FR-SCH-09
def test_a_run_records_what_started_it_and_what_it_was(runs):
    run_id = start(runs, triggered_by=Trigger.MANUAL, mode="full", dry_run=True, forced=True)
    runs.finish_run(
        run_id, finished_at=T0, counts={"content_stored": 4, "content_purged": 1}, checkpoint="abc"
    )
    got = runs.get_run(run_id)
    assert (got.triggered_by, got.mode, got.dry_run, got.forced) == (Trigger.MANUAL, "full", True, True)
    assert (got.content_stored, got.content_purged, got.checkpoint) == (4, 1, "abc")


# Covers: FR-SCH-10
def test_only_scheduled_finished_real_runs_set_the_cadence(runs):
    first = start(runs, at=T0)
    runs.finish_run(first, finished_at=T0, counts={})
    later = start(runs, at=T0 + timedelta(minutes=15))
    runs.finish_run(later, finished_at=T0, counts={})
    manual = start(runs, at=T0 + timedelta(hours=1), triggered_by=Trigger.MANUAL)
    runs.finish_run(manual, finished_at=T0, counts={})
    dry = start(runs, at=T0 + timedelta(hours=2), dry_run=True)
    runs.finish_run(dry, finished_at=T0, counts={})
    start(runs, at=T0 + timedelta(hours=3))  # still running

    assert runs.last_scheduled() == {"incremental:jira": T0 + timedelta(minutes=15)}


def test_runs_are_listed_newest_first_filtered_and_paged(runs):
    ids = []
    for index, source in enumerate(["a", "b", "a", "b", "a"]):
        run_id = start(runs, source=source, at=T0 + timedelta(minutes=index))
        runs.finish_run(run_id, finished_at=T0, counts={}, error="boom" if index == 4 else None)
        ids.append(run_id)

    assert [r.id for r in runs.list_runs()] == ids[::-1]
    assert [r.id for r in runs.list_runs(source_id="a")] == [ids[4], ids[2], ids[0]]
    assert [r.id for r in runs.list_runs(status=RunStatus.ERROR)] == [ids[4]]
    assert [r.id for r in runs.list_runs(before_id=ids[2], limit=2)] == [ids[1], ids[0]]
    assert runs.get_run(10_000) is None


def test_the_last_days_runs_are_counted_by_outcome(runs):
    for index, error in enumerate([None, None, "x"]):
        run_id = start(runs, at=T0 + timedelta(minutes=index))
        runs.finish_run(run_id, finished_at=T0, counts={}, error=error)
    start(runs, at=T0 + timedelta(minutes=9))
    old = start(runs, at=T0 - timedelta(days=3))
    runs.finish_run(old, finished_at=T0, counts={})

    stats = runs.run_stats(since=T0 - timedelta(days=1))

    assert (stats.runs, stats.succeeded, stats.failed, stats.running) == (4, 2, 1, 1)
    assert stats.success_rate == pytest.approx(2 / 3, abs=1e-3)
    assert runs.run_stats(since=T0 + timedelta(days=1)).success_rate is None


# Covers: FR-SCH-11
def test_requests_are_served_oldest_first_exactly_once(queue):
    first = queue.enqueue(SyncRequest(source_id="a", mode="full"), at=T0)
    second = queue.enqueue(SyncRequest(source_id="b", mode="incremental", dry_run=True), at=T0 + timedelta(seconds=1))

    claimed = queue.claim_next(at=T0 + timedelta(seconds=5))
    assert claimed.id == first and claimed.status is RequestStatus.RUNNING and claimed.mode == "full"
    assert queue.claim_next(at=T0).id == second
    assert queue.claim_next(at=T0) is None

    queue.finish_request(first, at=T0, status=RequestStatus.DONE, run_ids=[7, 8])
    done = next(r for r in queue.list_requests() if r.id == first)
    assert done.status is RequestStatus.DONE and done.run_ids == (7, 8)


# Covers: FR-SCH-11
def test_a_request_can_be_cancelled_only_until_it_starts(queue):
    waiting = queue.enqueue(SyncRequest(source_id="a", mode="full"), at=T0)
    started = queue.enqueue(SyncRequest(source_id="b", mode="full"), at=T0 + timedelta(seconds=1))
    assert queue.cancel(waiting, at=T0) is True
    assert queue.claim_next(at=T0).id == started
    assert queue.cancel(started, at=T0) is False
    assert queue.claim_next(at=T0) is None


# Covers: FR-SCH-11
def test_requests_left_running_are_failed_at_startup(queue):
    queue.enqueue(SyncRequest(source_id="a", kind=RequestKind.PURGE_CONTENT), at=T0)
    queue.claim_next(at=T0)
    assert queue.fail_running(at=T0, error="gone") == 1
    [only] = queue.list_requests()
    assert (only.status, only.error, only.kind) == (RequestStatus.FAILED, "gone", RequestKind.PURGE_CONTENT)
    assert queue.list_requests(statuses=[RequestStatus.QUEUED]) == []


# Covers: FR-SCH-12
def test_the_heartbeat_is_one_row_that_is_rewritten(heartbeats, store):
    assert heartbeats.last_beat() is None
    heartbeats.beat(Heartbeat(beat_at=T0, credentials={"jira": {"KB_JIRA_PAT": True}}))
    heartbeats.beat(
        Heartbeat(beat_at=T0 + timedelta(seconds=10), approved=False, approved_from="environment",
                  summarizer="failing", summarizer_error="403", working_on="full_scrape jira")
    )

    beat = heartbeats.last_beat()
    assert beat.beat_at == T0 + timedelta(seconds=10) and beat.approved is False
    assert (beat.approved_from, beat.summarizer, beat.working_on) == ("environment", "failing", "full_scrape jira")
    with store.connection.cursor() as cursor:
        cursor.execute("SELECT count(*) AS n FROM scheduler_heartbeat")
        assert cursor.fetchone()["n"] == 1
