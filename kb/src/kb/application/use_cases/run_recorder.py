"""Write a run's history at both ends, without ever breaking the run.

The scheduler and the CLI both sync, and both must leave the same record
(FR-SCH-08, FR-CLI-06). This is the one place that knows how: begin before the
work, end after it, and never let a failure to *write the history* take the work
down with it -- losing a run's record is bad; losing the run over it is worse.
"""

from __future__ import annotations

import logging
from typing import Mapping, Sequence

from kb.application.ports.clock import Clock
from kb.application.ports.run_log import RunLog
from kb.domain.runs import Trigger

logger = logging.getLogger("kb.runs")


class RunRecorder:
    def __init__(self, runs: RunLog | None, clock: Clock) -> None:
        #: None when nothing keeps history (a bare test double): every call is
        #: then a no-op rather than a branch at each call site.
        self._runs = runs
        self._clock = clock

    def begin(
        self,
        *,
        source_id: str,
        mechanism: str,
        triggered_by: Trigger,
        mode: str | None = None,
        dry_run: bool = False,
        forced: bool = False,
    ) -> int | None:
        if self._runs is None:
            return None
        try:
            return self._runs.start_run(
                source_id=source_id,
                mechanism=mechanism,
                started_at=self._clock.now(),
                triggered_by=triggered_by,
                mode=mode,
                dry_run=dry_run,
                forced=forced,
            )
        except Exception:
            logger.exception("could not record the start of a run")
            return None

    def end(
        self,
        run_id: int | None,
        *,
        counts: Mapping[str, int] | None = None,
        rejected: Sequence[str] = (),
        checkpoint: str | None = None,
        error: str | None = None,
    ) -> None:
        if self._runs is None or run_id is None:
            return
        try:
            self._runs.finish_run(
                run_id,
                finished_at=self._clock.now(),
                counts=counts or {},
                rejected=rejected,
                checkpoint=checkpoint,
                error=error,
            )
        except Exception:
            logger.exception("could not record the end of run %s", run_id)
