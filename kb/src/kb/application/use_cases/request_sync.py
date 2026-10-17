"""Ask the scheduler to sync, or to forget a source's kept text.

The console cannot do either itself -- it holds no source credentials and never
writes the catalog (FR-ADM-04) -- so what it does is write a request down
(FR-SCH-11). This is the one place that decides what a valid request is.
"""

from __future__ import annotations

from typing import Collection

from kb.application.ports.clock import Clock
from kb.application.ports.sync_queue import (
    ALL_SOURCES,
    RequestKind,
    RequestStatus,
    SyncQueue,
    SyncRequest,
)

MODES = ("incremental", "full")


class InvalidRequest(ValueError):
    """The request cannot be queued; the message says why, for the operator."""


class RequestSync:
    def __init__(self, queue: SyncQueue, clock: Clock) -> None:
        self._queue = queue
        self._clock = clock

    def sync(
        self,
        *,
        source_id: str,
        known_sources: Collection[str],
        mode: str = "incremental",
        dry_run: bool = False,
        forced: bool = False,
        requested_by: str = "admin",
    ) -> int:
        if mode not in MODES:
            raise InvalidRequest(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
        if source_id != ALL_SOURCES and source_id not in known_sources:
            raise InvalidRequest(f"no source with id {source_id!r}")
        if forced and dry_run:
            # A dry run makes no model calls; forcing re-summarization of
            # documents it will not write would promise something it cannot do.
            raise InvalidRequest("a dry run cannot be forced: it summarizes nothing")
        return self._enqueue(
            SyncRequest(
                kind=RequestKind.SYNC,
                source_id=source_id,
                mode=mode,
                dry_run=dry_run,
                forced=forced,
                requested_by=requested_by,
            )
        )

    def purge_content(
        self, *, source_id: str, known_sources: Collection[str], requested_by: str = "admin"
    ) -> int:
        if source_id not in known_sources:
            raise InvalidRequest(f"no source with id {source_id!r}")
        return self._enqueue(
            SyncRequest(kind=RequestKind.PURGE_CONTENT, source_id=source_id, requested_by=requested_by)
        )

    def cancel(self, request_id: int) -> bool:
        return self._queue.cancel(request_id, at=self._clock.now())

    def _enqueue(self, request: SyncRequest) -> int:
        # Pressing the button twice must not do the work twice: an identical
        # request already waiting is the answer.
        for waiting in self._queue.list_requests(statuses=[RequestStatus.QUEUED], limit=200):
            if (
                waiting.kind, waiting.source_id, waiting.mode, waiting.dry_run, waiting.forced,
            ) == (request.kind, request.source_id, request.mode, request.dry_run, request.forced):
                return waiting.id
        return self._queue.enqueue(request, at=self._clock.now())
