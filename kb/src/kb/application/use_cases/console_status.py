"""One word for "is this thing working?", and the reasons behind it.

The overview needs a verdict an operator can read at a glance, and a list of
what is wrong in words. Deciding it here -- a pure function of what the
scheduler and the run history say -- keeps the console from growing its own
opinion about health that the CLI would then disagree with.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from kb.application.ports.heartbeat import Heartbeat
from kb.application.ports.run_log import RunStats

#: A heartbeat older than this means the scheduler is not running: it beats on
#: every tick (ten seconds) and from its own thread while a long job holds the
#: tick, so a minute of silence is a dead process, not a busy one.
SCHEDULER_SILENCE = timedelta(seconds=60)


class Health(str, Enum):
    HEALTHY = "healthy"
    SYNCING = "syncing"
    DEGRADED = "degraded"
    ERROR = "error"


class Assessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    health: Health
    scheduler_alive: bool
    #: Seconds since the scheduler last reported; None if it never has.
    scheduler_silent_for: float | None
    #: What is wrong, in words an operator can act on. Empty when healthy.
    problems: tuple[str, ...] = Field(default_factory=tuple)


def assess(
    *,
    heartbeat: Heartbeat | None,
    now: datetime,
    stats: RunStats,
    running_requests: int = 0,
    sources_enabled: int = 0,
) -> Assessment:
    problems: list[str] = []
    silent_for = None if heartbeat is None else max(0.0, (now - heartbeat.beat_at).total_seconds())
    alive = heartbeat is not None and (now - heartbeat.beat_at) <= SCHEDULER_SILENCE

    if heartbeat is None:
        problems.append("The scheduler has never reported. Is kb-scheduler running?")
    elif not alive:
        problems.append(
            f"The scheduler has not reported for {int(silent_for // 60)} min {int(silent_for % 60)} s. "
            "Check `make logs SERVICE=kb-scheduler`."
        )

    degraded = False
    if heartbeat is not None:
        if heartbeat.config_error:
            degraded = True
            problems.append(f"The source configuration cannot be read: {heartbeat.config_error}")
        if not heartbeat.approved:
            degraded = True
            where = {
                "environment": "KB_SOURCES_APPROVED=false in the environment",
                "file": "`approved: false` in the source configuration",
            }.get(heartbeat.approved_from, "the source configuration")
            problems.append(f"Syncing is switched off ({where}).")
        if heartbeat.summarizer == "failing":
            degraded = True
            problems.append(
                "The summarizer failed its check, so scheduled runs refuse to catalogue "
                f"documents undescribed: {heartbeat.summarizer_error or 'no detail'}"
            )
    if stats.failed:
        degraded = True
        problems.append(f"{stats.failed} run(s) failed in the last 24 hours.")
    if sources_enabled == 0 and heartbeat is not None and not heartbeat.config_error:
        degraded = True
        problems.append("No source is enabled, so nothing will be catalogued.")

    busy = stats.running > 0 or running_requests > 0
    if heartbeat is None or not alive:
        health = Health.ERROR
    elif busy:
        health = Health.SYNCING
    elif degraded:
        health = Health.DEGRADED
    else:
        health = Health.HEALTHY
    return Assessment(
        health=health,
        scheduler_alive=alive,
        scheduler_silent_for=silent_for,
        problems=tuple(problems),
    )
