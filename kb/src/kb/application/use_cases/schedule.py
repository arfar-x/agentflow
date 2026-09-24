"""What should run, right now.

Deciding is separated from doing on purpose: a scheduler that mixes "is it time
yet?" into sleeping and HTTP calls can only be tested by waiting, and a
scheduling bug then takes a day to reproduce. This module is a pure function of
the configuration, the last run times and the clock -- every cadence rule below
is provable in milliseconds (FR-SCH-02).

The four mechanisms and why their cadences differ are in the spec, §9. What
matters here: a disabled mechanism produces no job at all (FR-SCH-01), and a
source already running produces none either, however slow that run is
(FR-SCH-05).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, ConfigDict

from kb.sources_config import (
    AnySource,
    Mechanisms,
    SourcesConfig,
    parse_duration,
    parse_time_of_day,
)

#: Fallbacks when the config says a mechanism is on but not how often. They
#: match the shipped defaults in kb-sources.yaml.example, so an operator who
#: deletes `every:` gets the documented behavior rather than silence.
DEFAULT_INCREMENTAL_SECONDS = 15 * 60
DEFAULT_FULL_SCRAPE_AT = (3, 0)
DEFAULT_REFRESH_DRAIN_SECONDS = 60


class JobKind(str, Enum):
    INCREMENTAL = "incremental"
    FULL_SCRAPE = "full_scrape"
    LAZY_REFRESH = "lazy_refresh"


class Job(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: JobKind
    #: None for the lazy-refresh drain, which is not tied to one source: it
    #: works through whatever entries readers have asked about.
    source_id: str | None = None
    #: Why this job is due, in words, for the log line that explains a run
    #: nobody asked for.
    reason: str = ""


class ScheduleState(BaseModel):
    """When each thing last ran, and what is running now.

    Held by the scheduler and passed in, rather than read from a database here,
    so the decision stays pure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    last_run: dict[str, datetime] = {}
    #: Keys currently executing. A long full scrape must not be started again
    #: on the next tick just because its cadence came round.
    running: frozenset[str] = frozenset()

    @staticmethod
    def key(kind: JobKind, source_id: str | None) -> str:
        return f"{kind.value}:{source_id or '*'}"


def due(
    config: SourcesConfig,
    state: ScheduleState,
    now: datetime,
    *,
    refresh_drain_seconds: float = DEFAULT_REFRESH_DRAIN_SECONDS,
) -> list[Job]:
    """Every job that should start at `now`, in the order to start them."""
    jobs: list[Job] = []

    if not config.approved:
        # The same switch sync itself honors. A scheduler that kept running
        # after somebody turned syncing off would make the switch useless --
        # and it is the one people reach for during an incident.
        return jobs

    for source in config.enabled_sources():
        mechanisms = config.mechanisms_for(source)
        jobs.extend(_source_jobs(source, mechanisms, state, now))

    if _any_lazy_refresh_enabled(config) and _is_due(
        state, JobKind.LAZY_REFRESH, None, now, refresh_drain_seconds
    ):
        jobs.append(
            Job(
                kind=JobKind.LAZY_REFRESH,
                reason=f"draining refresh requests every {int(refresh_drain_seconds)}s",
            )
        )

    return jobs


def _source_jobs(
    source: AnySource, mechanisms: Mechanisms, state: ScheduleState, now: datetime
) -> list[Job]:
    jobs: list[Job] = []

    if mechanisms.incremental.enabled:
        interval = parse_duration(
            mechanisms.incremental.every, default=DEFAULT_INCREMENTAL_SECONDS
        )
        if _is_due(state, JobKind.INCREMENTAL, source.id, now, interval):
            jobs.append(
                Job(
                    kind=JobKind.INCREMENTAL,
                    source_id=source.id,
                    reason=f"every {mechanisms.incremental.every or '15m'}",
                )
            )

    if mechanisms.full_scrape.enabled:
        at = parse_time_of_day(mechanisms.full_scrape.at) or DEFAULT_FULL_SCRAPE_AT
        if _daily_is_due(state, source.id, now, at):
            jobs.append(
                Job(
                    kind=JobKind.FULL_SCRAPE,
                    source_id=source.id,
                    reason=f"daily at {at[0]:02d}:{at[1]:02d}",
                )
            )

    return jobs


def _any_lazy_refresh_enabled(config: SourcesConfig) -> bool:
    return any(
        config.mechanisms_for(source).lazy_refresh.enabled
        for source in config.enabled_sources()
    )


def _is_due(
    state: ScheduleState,
    kind: JobKind,
    source_id: str | None,
    now: datetime,
    interval_seconds: float | None,
) -> bool:
    key = ScheduleState.key(kind, source_id)
    if key in state.running:
        return False
    if not interval_seconds:
        return False
    last = state.last_run.get(key)
    if last is None:
        # Never run: start now rather than waiting a full interval, so a
        # freshly enabled source is catalogued today, not tomorrow.
        return True
    return (now - last) >= timedelta(seconds=interval_seconds)


def _daily_is_due(
    state: ScheduleState, source_id: str | None, now: datetime, at: tuple[int, int]
) -> bool:
    key = ScheduleState.key(JobKind.FULL_SCRAPE, source_id)
    if key in state.running:
        return False

    scheduled_today = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    if now < scheduled_today:
        return False

    last = state.last_run.get(key)
    if last is None:
        # A scheduler that starts at 09:00 must not immediately run a nightly
        # job it "missed" -- but it also must not wait a whole day with no
        # record. Treat today's window as satisfied and start tomorrow.
        return False
    return last < scheduled_today
