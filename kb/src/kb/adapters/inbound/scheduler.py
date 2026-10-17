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
import os
import signal
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from kb.application.ports.heartbeat import Heartbeat
from kb.application.ports.sync_queue import (
    ALL_SOURCES,
    RequestKind,
    RequestStatus,
    SyncRequest,
)
from kb.application.use_cases.refresh_entries import RefreshEntries
from kb.application.use_cases.run_recorder import RunRecorder
from kb.application.use_cases.schedule import Job, JobKind, ScheduleState, due
from kb.application.use_cases.sync_source import Mode
from kb.domain.content import ContentPolicy
from kb.domain.runs import Trigger
from kb.sources_config import ConfigError, SourcesConfig, load

logger = logging.getLogger("kb.scheduler")

#: How often to ask "what is due?". Well below the shortest real cadence, so a
#: 15-minute job starts within seconds of its time, and cheap enough that the
#: answer being "nothing" costs nothing.
TICK_SECONDS = 10.0

#: How long a summarizer verdict is reused. The check is a real completion, so
#: several sources falling due on the same tick should cost one, not one each --
#: but short enough that a recovered endpoint is noticed by the next cadence.
SUMMARIZER_VERDICT_TTL = timedelta(seconds=60)


@dataclass
class _Result:
    """What one execution leaves behind, for the run's record."""

    counts: dict[str, int] = field(default_factory=dict)
    rejected: tuple[str, ...] = ()
    checkpoint: str | None = None


@dataclass
class _Outcome:
    run_id: int | None
    error: str | None


#: Left on a request that was running when the scheduler died.
REQUEST_INTERRUPTED = "the scheduler stopped before this request finished"


class Scheduler:
    def __init__(
        self,
        container: Any,
        *,
        sources_file: Path,
        tick_seconds: float = TICK_SECONDS,
        check_summarizer: Callable[[], dict[str, Any]] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._container = container
        self._sources_file = sources_file
        self._tick = tick_seconds
        #: None when no summarizer is configured: entries are then catalogued
        #: under their real titles, a deliberate and already-reported mode.
        self._check_summarizer = check_summarizer
        self._environ = os.environ if environ is None else environ
        self._verdict: tuple[datetime, str | None] | None = None
        self._state = ScheduleState()
        self._stop = threading.Event()
        self._started = False
        #: What the heartbeat says, refreshed by each tick and re-stamped by the
        #: thread that keeps beating while a long job holds the tick.
        self._pulse: Heartbeat | None = None
        self._working_on: str | None = None
        # Optional collaborators: a container without them (a unit test of one
        # concern) simply has no history, queue or heartbeat.
        self._runs = getattr(container, "runs", None)
        self._queue = getattr(container, "queue", None)
        self._heartbeats = getattr(container, "heartbeats", None)
        self._recorder = RunRecorder(self._runs, container.clock)

    # -- the loop ----------------------------------------------------------
    def run_forever(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: self.stop())
        logger.info("scheduler started; reading %s", self._sources_file)
        # A long sync holds the tick for minutes; the console must not read that
        # as a dead scheduler. Beating from its own thread, on its own database
        # connection, keeps the pulse honest in both directions: it stops when
        # the process does.
        threading.Thread(target=self._beat_forever, name="kb-heartbeat", daemon=True).start()
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

    # -- startup -----------------------------------------------------------
    def _start(self, now: datetime) -> None:
        """Once, before the first tick: recover from the last process's death
        and learn when each cadence last ran (FR-SCH-08, FR-SCH-10)."""
        if self._started:
            return
        self._started = True
        last_run: dict[str, datetime] = {}
        try:
            if self._runs is not None:
                closed = self._runs.close_interrupted(at=now)
                if closed:
                    logger.warning("closed %d run(s) the previous scheduler never finished", closed)
                last_run = dict(self._runs.last_scheduled())
            if self._queue is not None:
                self._queue.fail_running(at=now, error=REQUEST_INTERRUPTED)
        except Exception:
            logger.exception("could not recover state from the run history; starting fresh")
        self._state = ScheduleState(last_run=last_run, since=now)

    def tick(self, now: datetime) -> list[Job]:
        """Run whatever is due. Returns the jobs it ran, for tests and logs."""
        self._start(now)
        config: SourcesConfig | None
        try:
            config = load(self._sources_file)
            config_error = None
        except ConfigError as exc:
            # A file being edited right now is the normal case for this, so it
            # is a warning and a skipped tick, not a crash loop.
            logger.warning("not scheduling: %s", exc)
            config, config_error = None, str(exc)

        self._beat(now, config, config_error)
        # What a person asked for goes first: somebody is waiting on it.
        self._serve_requests(config, config_error)
        if config is None:
            return []

        jobs = due(config, self._state, now)
        for job in jobs:
            self._run(job, config, now)
        return jobs

    # -- the pulse ---------------------------------------------------------
    def _beat(self, now: datetime, config: SourcesConfig | None, config_error: str | None) -> None:
        if self._heartbeats is None:
            return
        from kb.adapters.outbound.source_factory import credential_presence

        summarizer, summarizer_error = "none", None
        if self._check_summarizer is not None:
            if self._verdict is None:
                summarizer = "unknown"
            elif self._verdict[1] is None:
                summarizer = "ok"
            else:
                summarizer, summarizer_error = "failing", self._verdict[1]

        self._pulse = Heartbeat(
            beat_at=now,
            approved=config.approved if config else True,
            approved_from=config.approved_from if config else "default",
            summarizer=summarizer,
            summarizer_error=summarizer_error,
            credentials=(
                {s.id: credential_presence(s, self._environ) for s in config.sources}
                if config
                else {}
            ),
            config_error=config_error,
            working_on=self._working_on,
        )
        self._write_pulse(self._pulse)

    def _write_pulse(self, pulse: Heartbeat) -> None:
        try:
            self._heartbeats.beat(pulse)
        except Exception:
            logger.exception("could not write the heartbeat")

    def _beat_forever(self) -> None:  # pragma: no cover -- a thread around _write_pulse
        while not self._stop.wait(self._tick):
            if self._pulse is not None:
                self._write_pulse(
                    self._pulse.model_copy(
                        update={"beat_at": self._now(), "working_on": self._working_on}
                    )
                )

    # -- running one scheduled job -----------------------------------------
    def _run(self, job: Job, config: SourcesConfig, now: datetime) -> None:
        key = ScheduleState.key(job.kind, job.source_id)
        if job.source_id and self._elsewhere_running(job.source_id):
            # The CLI (or another scheduler) is syncing this source right now:
            # FR-SCH-05. Not recorded as a run and `last_run` is untouched, so
            # the next tick tries again.
            logger.info("%s %s skipped: a run is already in progress", job.kind.value, job.source_id)
            return
        self._state = self._state.model_copy(
            update={"running": self._state.running | {key}}
        )
        try:
            logger.info("%s %s (%s)", job.kind.value, job.source_id or "", job.reason)
            self._perform(
                mechanism=job.kind.value,
                source_id=job.source_id,
                config=config,
                triggered_by=Trigger.SCHEDULED,
            )
        finally:
            # `last_run` moves whether the job succeeded or not: retrying a
            # broken source every ten seconds would hammer it and bury every
            # other job's log line.
            self._state = self._state.model_copy(
                update={
                    "running": self._state.running - {key},
                    "last_run": {**self._state.last_run, key: now},
                }
            )

    def _elsewhere_running(self, source_id: str) -> bool:
        if self._runs is None:
            return False
        try:
            return source_id in self._runs.running_sources()
        except Exception:
            logger.exception("could not ask which runs are in progress")
            return False

    def _perform(
        self,
        *,
        mechanism: str,
        source_id: str | None,
        config: SourcesConfig | None,
        triggered_by: Trigger,
        mode: str | None = None,
        dry_run: bool = False,
        forced: bool = False,
    ) -> _Outcome:
        """One run, recorded at both ends. A failure becomes the run's error."""
        label = source_id or "*"
        self._working_on = f"{mechanism} {label}"
        run_id = self._recorder.begin(
            source_id=label,
            mechanism=mechanism,
            triggered_by=triggered_by,
            mode=mode,
            dry_run=dry_run,
            forced=forced,
        )
        result, error = _Result(), None
        try:
            result = self._execute(
                mechanism, source_id, config, mode=mode, dry_run=dry_run, forced=forced,
                manual=triggered_by is not Trigger.SCHEDULED,
            )
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            logger.exception("%s for %s failed", mechanism, label)
        finally:
            self._working_on = None
        self._recorder.end(
            run_id,
            counts=result.counts,
            rejected=result.rejected,
            checkpoint=result.checkpoint,
            error=error,
        )
        return _Outcome(run_id, error)

    def _require_usable_summarizer(self) -> None:
        """FR-SCH-07: the check `sync` applies (FR-CFG-09), before a run that
        would summarize. Without it, an endpoint that answers /models but
        refuses completions makes this loop catalogue every new document
        undescribed -- a warning per document, nothing refused -- and the only
        recovery is a forced re-sync."""
        if self._check_summarizer is None:
            return
        now = self._now()
        if self._verdict is None or now - self._verdict[0] >= SUMMARIZER_VERDICT_TTL:
            result = self._check_summarizer()
            problem = None if result.get("ok") else (
                result.get("error") or "it failed its structured-output check"
            )
            self._verdict = (now, problem)
        problem = self._verdict[1]
        if problem is not None:
            raise RuntimeError(
                f"summarizer is not usable, so nothing was synced rather than catalogued "
                f"undescribed (see `kb check`): {problem}"
            )

    def _execute(
        self,
        mechanism: str,
        source_id: str | None,
        config: SourcesConfig | None,
        *,
        mode: str | None,
        dry_run: bool,
        forced: bool,
        manual: bool,
    ) -> _Result:
        if mechanism == "purge":
            purged = self._container.store.purge_content(source_id or "")
            return _Result(counts={"content_purged": purged})

        if config is None:
            raise RuntimeError("the source configuration cannot be read (see the heartbeat)")
        if manual:
            # A scheduled run never gets here with syncing off (`due` returns
            # nothing); a requested one must be refused the same way.
            config.require_approved()
        if not dry_run:
            self._require_usable_summarizer()

        if mechanism == JobKind.LAZY_REFRESH.value:
            report = self._refresher(config).execute()
            if report.failed:
                logger.warning("refresh failures: %s", "; ".join(report.failed))
            return _Result(counts={"updated": report.refreshed, "unchanged": report.unchanged})

        from kb.adapters.outbound.source_factory import build_source

        source_config = config.source(source_id or "")
        source = build_source(source_config)
        sync_mode = Mode.FULL if mechanism == JobKind.FULL_SCRAPE.value else Mode.INCREMENTAL
        checkpoint = self._container.store.get_checkpoint(source.source_id)

        report = self._container.sync.execute(
            source,
            mode=sync_mode,
            dry_run=dry_run,
            force=forced,
            checkpoint=checkpoint,
            content=config.content_policy_for(source_config),
        )
        if report.checkpoint and not dry_run:
            # Only after the run returned: a checkpoint advanced past a failure
            # would skip whatever that run never saw.
            self._container.store.set_checkpoint(source.source_id, report.checkpoint)
        if report.rejected:
            logger.warning("%s rejected %d documents: %s", source.source_id,
                           len(report.rejected), "; ".join(report.rejected[:5]))
        return _Result(
            counts=report.model_dump(
                include={
                    "seen", "created", "updated", "unchanged", "revived", "missing",
                    "summarized", "content_stored", "content_purged",
                }
            ),
            rejected=report.rejected,
            checkpoint=report.checkpoint,
        )

    def _refresher(self, config: SourcesConfig) -> RefreshEntries:
        from kb.adapters.outbound.source_factory import build_source

        def resolve(source_id: str):
            for source in config.enabled_sources():
                if source.id == source_id:
                    return build_source(source)
            return None

        def resolve_content(source_id: str) -> ContentPolicy:
            for source in config.sources:
                if source.id == source_id:
                    return config.content_policy_for(source)
            return ContentPolicy.off()

        return RefreshEntries(
            self._container.store,
            self._container.reconcile,  # the same funnel every mechanism uses
            resolve,
            self._container.clock,
            resolve_content=resolve_content,
        )

    # -- requests from the console (FR-SCH-11) ------------------------------
    def _serve_requests(self, config: SourcesConfig | None, config_error: str | None) -> None:
        if self._queue is None:
            return
        while not self._stop.is_set():
            try:
                request = self._queue.claim_next(at=self._now())
            except Exception:
                logger.exception("could not read the request queue")
                return
            if request is None:
                return
            self._serve(request, config, config_error)

    def _serve(self, request: SyncRequest, config: SourcesConfig | None, config_error: str | None) -> None:
        run_ids: list[int] = []
        errors: list[str] = []
        try:
            for target in self._targets(request, config):
                if self._elsewhere_running(target):
                    errors.append(f"{target}: a run is already in progress")
                    continue
                if request.kind is RequestKind.PURGE_CONTENT:
                    outcome = self._perform(
                        mechanism="purge", source_id=target, config=config,
                        triggered_by=Trigger.MANUAL,
                    )
                else:
                    full = request.mode == Mode.FULL.value
                    outcome = self._perform(
                        mechanism=JobKind.FULL_SCRAPE.value if full else JobKind.INCREMENTAL.value,
                        source_id=target,
                        config=config,
                        triggered_by=Trigger.MANUAL,
                        mode=Mode.FULL.value if full else Mode.INCREMENTAL.value,
                        dry_run=request.dry_run,
                        forced=request.forced,
                    )
                if outcome.run_id is not None:
                    run_ids.append(outcome.run_id)
                if outcome.error:
                    errors.append(f"{target}: {outcome.error}")
        except Exception as exc:
            errors.append(str(exc) or type(exc).__name__)
            logger.exception("request %s failed", request.id)
        if config is None and not run_ids and not errors:
            errors.append(f"the source configuration cannot be read: {config_error}")
        try:
            self._queue.finish_request(
                request.id,
                at=self._now(),
                status=RequestStatus.FAILED if errors else RequestStatus.DONE,
                run_ids=run_ids,
                error="; ".join(errors) or None,
            )
        except Exception:
            logger.exception("could not record the outcome of request %s", request.id)

    def _targets(self, request: SyncRequest, config: SourcesConfig | None) -> list[str]:
        if request.source_id != ALL_SOURCES:
            return [request.source_id]
        if config is None:
            return []
        return [source.id for source in config.enabled_sources()]


def main() -> None:  # pragma: no cover -- the container's entrypoint
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from kb.config import Settings
    from kb.container import build

    settings = Settings.from_env()
    container = build(settings)
    check = None
    if settings.summarizer_configured:
        from kb.container import build_summarizer

        check = build_summarizer(settings).check
    try:
        Scheduler(
            container, sources_file=Path(settings.sources_file), check_summarizer=check
        ).run_forever()
    finally:
        container.close()


if __name__ == "__main__":  # pragma: no cover
    main()
