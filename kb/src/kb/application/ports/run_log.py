"""The history of sync runs.

Written at both ends of a run (FR-SCH-08): the first write is what makes a run
in progress visible, and what lets the next start of the scheduler recognise a
run its predecessor never finished.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from kb.domain.runs import RunStatus, Trigger


class RunRecord(BaseModel):
    """One run, as read back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    source_id: str
    mechanism: str
    status: RunStatus
    triggered_by: Trigger = Trigger.SCHEDULED
    mode: str | None = None
    dry_run: bool = False
    forced: bool = False
    started_at: datetime
    finished_at: datetime | None = None
    seen: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    revived: int = 0
    missing: int = 0
    summarized: int = 0
    content_stored: int = 0
    content_purged: int = 0
    #: Each rejected document, named with the reason.
    rejected: tuple[str, ...] = Field(default_factory=tuple)
    checkpoint: str | None = None
    error: str | None = None

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)


class RunStats(BaseModel):
    """How the recent runs went, for the overview."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runs: int = 0
    succeeded: int = 0
    warned: int = 0
    failed: int = 0
    running: int = 0

    @property
    def success_rate(self) -> float | None:
        """Over runs that ended; None when none have, rather than a made-up 100."""
        finished = self.succeeded + self.warned + self.failed
        if finished == 0:
            return None
        return round((self.succeeded + self.warned) / finished, 4)


@runtime_checkable
class RunLog(Protocol):
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
        """Record that a run has begun and return its id."""
        ...

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
        """Record how it ended. The status is derived (`domain.runs.final_status`)."""
        ...

    def close_interrupted(self, *, at: datetime) -> int:
        """Close every run still `running` as interrupted; returns how many."""
        ...

    def running_sources(self) -> set[str]:
        """Sources with a run in progress -- in any process, so the CLI and the
        scheduler cannot both be syncing one source (FR-SCH-05)."""
        ...

    def last_scheduled(self) -> dict[str, datetime]:
        """When each cadence last ran on its own, keyed `mechanism:source_id`
        (`*` for the refresh drain) -- the same key `ScheduleState` uses, so the
        scheduler can seed itself from this at startup (FR-SCH-10). Runs a
        person asked for do not count: a manual sync is not the nightly one."""
        ...

    def list_runs(
        self,
        *,
        source_id: str | None = None,
        status: RunStatus | None = None,
        mode: str | None = None,
        before_id: int | None = None,
        limit: int = 50,
    ) -> list[RunRecord]: ...

    def get_run(self, run_id: int) -> RunRecord | None: ...

    def run_stats(self, *, since: datetime) -> RunStats: ...
