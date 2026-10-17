"""What a sync run is, as a fact about the past.

A run is written when it starts and finished when it ends (FR-SCH-08), so these
rules are the ones both ends agree on: which states exist, who may have started
one, and how a finished run is judged.
"""

from __future__ import annotations

from enum import Enum
from typing import Sequence


class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    #: Finished, but documents were rejected. Not an error -- one bad page must
    #: not end a run over a 900-page space -- but not "nothing to see" either.
    WARNING = "warning"
    ERROR = "error"


class Trigger(str, Enum):
    SCHEDULED = "scheduled"
    #: Asked for from the admin console.
    MANUAL = "manual"
    WEBHOOK = "webhook"
    CLI = "cli"


def final_status(*, error: str | None, rejected: Sequence[str]) -> RunStatus:
    """How a run that has ended is judged: an error beats a warning beats
    success. Kept here, not in SQL, so the console and the CLI cannot disagree
    about what counts as a warning."""
    if error:
        return RunStatus.ERROR
    if rejected:
        return RunStatus.WARNING
    return RunStatus.SUCCESS


#: The scheduler closes a run it finds still `running` at startup with this
#: error: the process that owned it is gone, whatever it was doing.
INTERRUPTED = "interrupted: the scheduler stopped before this run finished"
