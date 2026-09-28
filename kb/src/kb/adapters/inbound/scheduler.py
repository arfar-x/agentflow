"""The service that runs sync so nobody has to remember to.

Its own container, not a thread inside the MCP server: a full scrape of a large
space can run for minutes, and a search must never wait behind one (NFR-DEP-03).
It is also the only component holding both source credentials and a writable
database role.

The thinking is elsewhere. `use_cases/schedule.due()` decides what should run,
as a pure function of the config, the last run times and the clock; this module
sleeps, executes, records, and keeps going. That split is why a cadence rule can
be tested in milliseconds instead of by waiting a day for 03:00.

**A failure here is never fatal.** One source that is down must not stop the
others, and must not stop the scheduler: the error is recorded against the run
and the next cadence is attempted (FR-SCH-03).
"""

from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kb.application.use_cases.refresh_entries import RefreshEntries
from kb.application.use_cases.schedule import Job, JobKind, ScheduleState, due
from kb.application.use_cases.sync_source import Mode
from kb.sources_config import ConfigError, SourcesConfig, load

logger = logging.getLogger("kb.scheduler")

#: How often to ask "what is due?". Well below the shortest real cadence, so a
#: 15-minute job starts within seconds of its time, and cheap enough that the
#: answer being "nothing" costs nothing.
TICK_SECONDS = 10.0


class Scheduler:
    def __init__(
        self,
        container: Any,
        *,
        sources_file: Path,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self._container = container
        self._sources_file = sources_file
        self._tick = tick_seconds
        self._state = ScheduleState()
        self._stop = threading.Event()

    # -- the loop ----------------------------------------------------------
    def run_forever(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: self.stop())
        logger.info("scheduler started; reading %s", self._sources_file)
        while not self._stop.is_set():
            try:
                self.tick(self._now())
            except Exception:
                # Nothing in a tick is worth dying for: the config may be
                # half-edited, a source may be unreachable. Log it and take the
                # next tick.
                logger.exception("tick failed; continuing")
            self._stop.wait(self._tick)
        logger.info("scheduler stopped")

    def stop(self) -> None:
        self._stop.set()

    def _now(self) -> datetime:
        clock = getattr(self._container, "clock", None)
        return clock.now() if clock is not None else datetime.now(timezone.utc)

    def tick(self, now: datetime) -> list[Job]:
        """Run whatever is due. Returns the jobs it ran, for tests and logs."""
        try:
            config = load(self._sources_file)
        except ConfigError as exc:
            # A file being edited right now is the normal case for this, so it
            # is a warning and a skipped tick, not a crash loop.
            logger.warning("not scheduling: %s", exc)
            return []

        jobs = due(config, self._state, now)
        for job in jobs:
            self._run(job, config, now)
        return jobs

    # -- running one job ---------------------------------------------------
    def _run(self, job: Job, config: SourcesConfig, now: datetime) -> None:
        key = ScheduleState.key(job.kind, job.source_id)
        self._state = self._state.model_copy(
            update={"running": self._state.running | {key}}
        )
        started = now
        error: str | None = None
        counts: dict[str, int] = {}

        try:
            logger.info("%s %s (%s)", job.kind.value, job.source_id or "", job.reason)
            counts = self._execute(job, config)
        except Exception as exc:
            error = str(exc)
            logger.exception("%s for %s failed", job.kind.value, job.source_id)
        finally:
            # The container's clock, not the wall clock: mixing the two makes a
            # run's duration nonsense the moment time is injected, and the only
            # place that shows up is a test.
            finished = self._now()
            # `last_run` moves whether the job succeeded or not: retrying a
            # broken source every ten seconds would hammer it and bury every
            # other job's log line.
            self._state = self._state.model_copy(
                update={
                    "running": self._state.running - {key},
                    "last_run": {**self._state.last_run, key: started},
                }
            )
            self._record(job, started, finished, counts, error)

    def _execute(self, job: Job, config: SourcesConfig) -> dict[str, int]:
        if job.kind is JobKind.LAZY_REFRESH:
            report = self._refresher(config).execute()
            if report.failed:
                logger.warning("refresh failures: %s", "; ".join(report.failed))
            return {"updated": report.refreshed, "unchanged": report.unchanged}

        from kb.adapters.outbound.source_factory import build_source

        source_config = config.source(job.source_id or "")
        source = build_source(source_config)
        mode = Mode.FULL if job.kind is JobKind.FULL_SCRAPE else Mode.INCREMENTAL
        checkpoint = self._container.store.get_checkpoint(source.source_id)

        report = self._container.sync.execute(source, mode=mode, checkpoint=checkpoint)
        if report.checkpoint:
            # Only after the run returned: a checkpoint advanced past a failure
            # would skip whatever that run never saw.
            self._container.store.set_checkpoint(source.source_id, report.checkpoint)
        if report.rejected:
            logger.warning("%s rejected %d documents: %s", source.source_id,
                           len(report.rejected), "; ".join(report.rejected[:5]))
        return {
            "created": report.created,
            "updated": report.updated,
            "unchanged": report.unchanged,
            "revived": report.revived,
            "missing": report.missing,
            "summarized": report.summarized,
        }

    def _refresher(self, config: SourcesConfig) -> RefreshEntries:
        from kb.adapters.outbound.source_factory import build_source

        def resolve(source_id: str):
            for source in config.enabled_sources():
                if source.id == source_id:
                    return build_source(source)
            return None

        return RefreshEntries(
            self._container.store,
            self._container.reconcile,  # the same funnel every mechanism uses
            resolve,
            self._container.clock,
        )

    def _record(
        self,
        job: Job,
        started: datetime,
        finished: datetime,
        counts: dict[str, int],
        error: str | None,
    ) -> None:
        try:
            self._container.store.record_run(
                source_id=job.source_id or "*",
                mechanism=job.kind.value,
                started_at=started,
                finished_at=finished,
                counts=counts,
                error=error,
            )
        except Exception:
            # Losing the history of a run is bad; losing the scheduler because
            # we failed to write that history is worse.
            logger.exception("could not record the run")


def main() -> None:  # pragma: no cover -- the container's entrypoint
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from kb.config import Settings
    from kb.container import build

    settings = Settings.from_env()
    container = build(settings)
    try:
        Scheduler(container, sources_file=Path(settings.sources_file)).run_forever()
    finally:
        container.close()


if __name__ == "__main__":  # pragma: no cover
    main()
