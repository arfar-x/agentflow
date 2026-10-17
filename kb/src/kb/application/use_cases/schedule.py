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
    #: When this state began -- the scheduler's start. A nightly job with no
    #: recorded run counts as having "run" then, so the first one is the next
    #: time of day rather than never (see `_daily_is_due`).
    since: datetime | None = None
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

    # `now` arrives in the deployment's own zone (see ports/clock.py), so
    # replacing the hour gives the local 03:00 an operator meant -- not 03:00
    # UTC, which in much of the world is the middle of the working day.
    scheduled_today = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    if now < scheduled_today:
        return False

    # A scheduler that starts at 09:00 must not immediately run a nightly job it
    # "missed" -- but it also must not wait forever for a record that never
    # comes. With no run on record, treat the moment it began as the last one:
    # today's window is satisfied and the first nightly run is tomorrow's.
    last = state.last_run.get(key) or state.since
    if last is None:
        return False
    return last < scheduled_today


class ScheduleRow(BaseModel):
    """One line of the schedule view: a mechanism of a source, and when it is
    next due (FR-ADM-07)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: JobKind
    #: None for the refresh drain, which belongs to no one source.
    source_id: str | None = None
    #: Whether it will run at all.
    enabled: bool
    #: Why not, when it will not.
    reason: str | None = None
    cadence: str
    last_run: datetime | None = None
    #: When it falls due; at or before `now` means it runs on the next tick.
    #: None for what has no clock (a mechanism that is off, or lazy refresh,
    #: which runs when somebody reads a stale entry).
    due_at: datetime | None = None
    running: bool = False


def next_due(
    config: SourcesConfig,
    state: ScheduleState,
    now: datetime,
    *,
    refresh_drain_seconds: float = DEFAULT_REFRESH_DRAIN_SECONDS,
) -> list[ScheduleRow]:
    """Every source's mechanisms and when each is next due (FR-SCH-13).

    Written beside `due()` rather than derived from it so the answer for a time
    in the future does not need a clock to be stepped; the two are held
    together by a test that asks `due()` at each reported instant.
    """
    rows: list[ScheduleRow] = []
    for source in config.sources:
        mechanisms = config.mechanisms_for(source)
        off = _why_off(config, source.enabled)

        inc = mechanisms.incremental
        interval = parse_duration(inc.every, default=DEFAULT_INCREMENTAL_SECONDS)
        key = ScheduleState.key(JobKind.INCREMENTAL, source.id)
        last = state.last_run.get(key)
        rows.append(
            ScheduleRow(
                kind=JobKind.INCREMENTAL,
                source_id=source.id,
                enabled=bool(inc.enabled) and off is None,
                reason=off or (None if inc.enabled else "mechanism disabled"),
                cadence=f"every {inc.every or '15m'}",
                last_run=last,
                due_at=_incremental_due(last, interval, now) if inc.enabled and off is None else None,
                running=key in state.running,
            )
        )

        full = mechanisms.full_scrape
        at = parse_time_of_day(full.at) or DEFAULT_FULL_SCRAPE_AT
        key = ScheduleState.key(JobKind.FULL_SCRAPE, source.id)
        last = state.last_run.get(key)
        rows.append(
            ScheduleRow(
                kind=JobKind.FULL_SCRAPE,
                source_id=source.id,
                enabled=bool(full.enabled) and off is None,
                reason=off or (None if full.enabled else "mechanism disabled"),
                cadence=f"daily at {at[0]:02d}:{at[1]:02d}",
                last_run=last,
                due_at=_daily_due(last or state.since, at, now) if full.enabled and off is None else None,
                running=key in state.running,
            )
        )

        lazy = mechanisms.lazy_refresh
        rows.append(
            ScheduleRow(
                kind=JobKind.LAZY_REFRESH,
                source_id=source.id,
                enabled=bool(lazy.enabled) and off is None,
                reason=off or (None if lazy.enabled else "mechanism disabled"),
                cadence=f"on read, stale after {lazy.stale_after or '24h'}",
            )
        )

    drain_on = config.approved and _any_lazy_refresh_enabled(config)
    key = ScheduleState.key(JobKind.LAZY_REFRESH, None)
    last = state.last_run.get(key)
    rows.append(
        ScheduleRow(
            kind=JobKind.LAZY_REFRESH,
            source_id=None,
            enabled=drain_on,
            reason=None if drain_on else _why_off(config, True) or "no source uses lazy refresh",
            cadence=f"drain every {int(refresh_drain_seconds)}s",
            last_run=last,
            due_at=_incremental_due(last, refresh_drain_seconds, now) if drain_on else None,
            running=key in state.running,
        )
    )
    return rows


def _why_off(config: SourcesConfig, source_enabled: bool) -> str | None:
    if not config.approved:
        return "syncing is switched off"
    if not source_enabled:
        return "source disabled"
    return None


def _incremental_due(last: datetime | None, interval: float | None, now: datetime) -> datetime | None:
    if not interval:
        return None
    if last is None:
        return now
    return last + timedelta(seconds=interval)


def _daily_due(last: datetime | None, at: tuple[int, int], now: datetime) -> datetime | None:
    scheduled_today = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    if last is None:
        # Nothing on record and no start time: `due()` has no basis to start it.
        return None
    # Ran already today (at or after the hour): tomorrow's. Otherwise one is
    # owed -- now, if the hour has passed, else when it does.
    return scheduled_today if last < scheduled_today else scheduled_today + timedelta(days=1)
