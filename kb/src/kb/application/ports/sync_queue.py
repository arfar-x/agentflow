"""Work the admin console asks the scheduler to do (FR-SCH-11).

The console holds no source credentials and never writes the catalog, so it
cannot sync anything itself. It writes a request here; the scheduler, which has
both, picks requests up one at a time.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class RequestKind(str, Enum):
    SYNC = "sync"
    PURGE_CONTENT = "purge_content"
    #: Soft-delete everything a source produced. For a source that is gone from
    #: the configuration: nothing else would ever retire its entries.
    RETIRE_SOURCE = "retire_source"


class RequestStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Stands for "every enabled source" in a request.
ALL_SOURCES = "*"


class SyncRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int = 0
    kind: RequestKind = RequestKind.SYNC
    source_id: str = Field(min_length=1)
    #: "incremental" or "full"; unused by a purge.
    mode: str | None = None
    dry_run: bool = False
    forced: bool = False
    status: RequestStatus = RequestStatus.QUEUED
    requested_at: datetime | None = None
    requested_by: str = "admin"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    run_ids: tuple[int, ...] = ()
    error: str | None = None


@runtime_checkable
class SyncQueue(Protocol):
    def enqueue(self, request: SyncRequest, *, at: datetime) -> int: ...

    def claim_next(self, *, at: datetime) -> SyncRequest | None:
        """Take the oldest queued request and mark it running."""
        ...

    def finish_request(
        self,
        request_id: int,
        *,
        at: datetime,
        status: RequestStatus,
        run_ids: Sequence[int] = (),
        error: str | None = None,
    ) -> None: ...

    def cancel(self, request_id: int, *, at: datetime) -> bool:
        """Cancel a request that has not started. False when it already has."""
        ...

    def list_requests(
        self, *, statuses: Sequence[RequestStatus] | None = None, limit: int = 50
    ) -> list[SyncRequest]: ...

    def fail_running(self, *, at: datetime, error: str) -> int:
        """At startup: requests left `running` by a scheduler that died."""
        ...
