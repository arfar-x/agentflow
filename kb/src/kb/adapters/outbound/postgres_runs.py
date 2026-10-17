"""Run history, the request queue and the scheduler's heartbeat, in Postgres.

Three small stores over one connection, kept apart from `PostgresEntryStore`
because they are operational state, not catalog: the console connects as a role
that may read all of this and write only its own part (kb/grants/admin.sql).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from kb.application.ports.heartbeat import Heartbeat
from kb.application.ports.run_log import RunRecord, RunStats
from kb.application.ports.sync_queue import RequestKind, RequestStatus, SyncRequest
from kb.domain.runs import INTERRUPTED, RunStatus, Trigger, final_status

_RUN_COLUMNS = (
    "id, source_id, mechanism, status, triggered_by, mode, dry_run, forced, started_at,"
    " finished_at, seen, created, updated, unchanged, revived, missing, summarized,"
    " content_stored, content_purged, rejected, checkpoint, error"
)
_COUNTS = (
    "seen", "created", "updated", "unchanged", "revived", "missing", "summarized",
    "content_stored", "content_purged",
)


class _Base:
    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = dict_row


class PostgresRunLog(_Base):
    """Implements `kb.application.ports.run_log.RunLog`."""

    def start_run(
        self,
        *,
        source_id: str,
        mechanism: str,
        started_at: datetime,
        triggered_by: Trigger = Trigger.SCHEDULED,
        mode: str | None = None,
        dry_run: bool = False,
        forced: bool = False,
    ) -> int:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sync_run (source_id, mechanism, started_at, status, triggered_by,
                                      mode, dry_run, forced)
                VALUES (%s, %s, %s, 'running', %s, %s, %s, %s)
                RETURNING id
                """,
                (source_id, mechanism, started_at, Trigger(triggered_by).value, mode, dry_run, forced),
            )
            return cursor.fetchone()["id"]

    def finish_run(
        self,
        run_id: int,
        *,
        finished_at: datetime,
        counts: Mapping[str, int],
        rejected: Sequence[str] = (),
        checkpoint: str | None = None,
        error: str | None = None,
    ) -> None:
        status = final_status(error=error, rejected=rejected)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE sync_run
                   SET finished_at = %(finished_at)s, status = %(status)s,
                       {", ".join(f"{name} = %({name})s" for name in _COUNTS)},
                       rejected = %(rejected)s, checkpoint = %(checkpoint)s, error = %(error)s
                 WHERE id = %(id)s
                """,
                {
                    "id": run_id,
                    "finished_at": finished_at,
                    "status": status.value,
                    **{name: int(counts.get(name, 0)) for name in _COUNTS},
                    "rejected": Jsonb([str(item)[:500] for item in rejected][:200]),
                    "checkpoint": checkpoint,
                    "error": error[:2000] if error else None,
                },
            )

    def close_interrupted(self, *, at: datetime) -> int:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "UPDATE sync_run SET status = 'error', finished_at = %s, error = %s"
                " WHERE status = 'running'",
                (at, INTERRUPTED),
            )
            return cursor.rowcount

    def running_sources(self) -> set[str]:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT source_id FROM sync_run WHERE status = 'running'")
            return {row["source_id"] for row in cursor.fetchall()}

    def last_scheduled(self) -> dict[str, datetime]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT mechanism || ':' || source_id AS key, max(started_at) AS at
                  FROM sync_run
                 WHERE triggered_by = 'scheduled' AND NOT dry_run AND status <> 'running'
                 GROUP BY mechanism, source_id
                """
            )
            return {row["key"]: row["at"] for row in cursor.fetchall()}

    def list_runs(
        self,
        *,
        source_id: str | None = None,
        status: RunStatus | None = None,
        mode: str | None = None,
        before_id: int | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_RUN_COLUMNS} FROM sync_run
                 WHERE (%(source_id)s::text IS NULL OR source_id = %(source_id)s)
                   AND (%(status)s::text IS NULL OR status = %(status)s)
                   AND (%(mode)s::text IS NULL OR mode = %(mode)s OR mechanism = %(mode)s)
                   AND (%(before)s::bigint IS NULL OR id < %(before)s)
                 ORDER BY id DESC
                 LIMIT %(limit)s
                """,
                {
                    "source_id": source_id,
                    "status": status.value if status else None,
                    "mode": mode,
                    "before": before_id,
                    "limit": max(1, min(limit, 200)),
                },
            )
            return [_run(row) for row in cursor.fetchall()]

    def get_run(self, run_id: int) -> RunRecord | None:
        with self._connection.cursor() as cursor:
            cursor.execute(f"SELECT {_RUN_COLUMNS} FROM sync_run WHERE id = %s", (run_id,))
            row = cursor.fetchone()
        return _run(row) if row else None

    def latest_per_source(self) -> dict[str, RunRecord]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT DISTINCT ON (source_id) {_RUN_COLUMNS}
                  FROM sync_run
                 ORDER BY source_id, id DESC
                """
            )
            return {row["source_id"]: _run(row) for row in cursor.fetchall()}

    def run_stats(self, *, since: datetime) -> RunStats:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)                                  AS runs,
                       count(*) FILTER (WHERE status = 'success') AS succeeded,
                       count(*) FILTER (WHERE status = 'warning') AS warned,
                       count(*) FILTER (WHERE status = 'error')   AS failed,
                       count(*) FILTER (WHERE status = 'running') AS running
                  FROM sync_run WHERE started_at >= %s
                """,
                (since,),
            )
            return RunStats.model_validate(dict(cursor.fetchone()))


def _run(row: Mapping[str, Any]) -> RunRecord:
    values = dict(row)
    values["rejected"] = tuple(values.get("rejected") or ())
    return RunRecord.model_validate(values)


_REQUEST_COLUMNS = (
    "id, kind, source_id, mode, dry_run, forced, status, requested_at, requested_by,"
    " started_at, finished_at, run_ids, error"
)


class PostgresSyncQueue(_Base):
    """Implements `kb.application.ports.sync_queue.SyncQueue`."""

    def enqueue(self, request: SyncRequest, *, at: datetime) -> int:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sync_request (kind, source_id, mode, dry_run, forced, requested_at,
                                          requested_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    request.kind.value, request.source_id, request.mode, request.dry_run,
                    request.forced, at, request.requested_by,
                ),
            )
            return cursor.fetchone()["id"]

    def claim_next(self, *, at: datetime) -> SyncRequest | None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE sync_request SET status = 'running', started_at = %s
                 WHERE id = (SELECT id FROM sync_request
                              WHERE status = 'queued'
                              ORDER BY requested_at, id
                              LIMIT 1
                                FOR UPDATE SKIP LOCKED)
                RETURNING {_REQUEST_COLUMNS}
                """,
                (at,),
            )
            row = cursor.fetchone()
        return _request(row) if row else None

    def finish_request(
        self,
        request_id: int,
        *,
        at: datetime,
        status: RequestStatus,
        run_ids: Sequence[int] = (),
        error: str | None = None,
    ) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "UPDATE sync_request SET status = %s, finished_at = %s, run_ids = %s, error = %s"
                " WHERE id = %s",
                (status.value, at, list(run_ids), error[:2000] if error else None, request_id),
            )

    def cancel(self, request_id: int, *, at: datetime) -> bool:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "UPDATE sync_request SET status = 'cancelled', finished_at = %s"
                " WHERE id = %s AND status = 'queued'",
                (at, request_id),
            )
            return cursor.rowcount == 1

    def list_requests(
        self, *, statuses: Sequence[RequestStatus] | None = None, limit: int = 50
    ) -> list[SyncRequest]:
        wanted = [s.value for s in statuses] if statuses else None
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_REQUEST_COLUMNS} FROM sync_request
                 WHERE (%(statuses)s::text[] IS NULL OR status = ANY(%(statuses)s))
                 ORDER BY id DESC LIMIT %(limit)s
                """,
                {"statuses": wanted, "limit": max(1, min(limit, 200))},
            )
            return [_request(row) for row in cursor.fetchall()]

    def fail_running(self, *, at: datetime, error: str) -> int:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "UPDATE sync_request SET status = 'failed', finished_at = %s, error = %s"
                " WHERE status = 'running'",
                (at, error),
            )
            return cursor.rowcount


def _request(row: Mapping[str, Any]) -> SyncRequest:
    values = dict(row)
    values["run_ids"] = tuple(values.get("run_ids") or ())
    return SyncRequest.model_validate(values)


class PostgresHeartbeat(_Base):
    """Implements `kb.application.ports.heartbeat.HeartbeatStore`."""

    def beat(self, heartbeat: Heartbeat) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO scheduler_heartbeat (id, beat_at, approved, approved_from, summarizer,
                                                 summarizer_error, credentials, config_error,
                                                 working_on)
                VALUES (1, %(beat_at)s, %(approved)s, %(approved_from)s, %(summarizer)s,
                        %(summarizer_error)s, %(credentials)s, %(config_error)s, %(working_on)s)
                ON CONFLICT (id) DO UPDATE SET
                    beat_at = EXCLUDED.beat_at, approved = EXCLUDED.approved,
                    approved_from = EXCLUDED.approved_from, summarizer = EXCLUDED.summarizer,
                    summarizer_error = EXCLUDED.summarizer_error,
                    credentials = EXCLUDED.credentials, config_error = EXCLUDED.config_error,
                    working_on = EXCLUDED.working_on
                """,
                {
                    **heartbeat.model_dump(),
                    "credentials": Jsonb(dict(heartbeat.credentials)),
                },
            )

    def last_beat(self) -> Heartbeat | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT beat_at, approved, approved_from, summarizer, summarizer_error,"
                " credentials, config_error, working_on FROM scheduler_heartbeat WHERE id = 1"
            )
            row = cursor.fetchone()
        return Heartbeat.model_validate(dict(row)) if row else None
