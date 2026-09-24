"""Time, injected.

Staleness, soft deletion and "last seen" all compare timestamps, so tests need
to control now() rather than sleep.

**Every timestamp is timezone-aware**, so comparisons are correct wherever the
containers think they are. The zone the clock reports in is a deployment
choice, because one thing genuinely depends on it: a nightly job configured for
`at: "03:00"` means 03:00 where the people who wrote that live, not 03:00 UTC.
Everything else -- staleness, "last seen", soft deletion -- compares instants
and is unaffected.
"""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def __init__(self, tz: tzinfo | None = None) -> None:
        #: UTC unless a deployment says otherwise (KB_TIMEZONE).
        self._tz = tz or timezone.utc

    def now(self) -> datetime:
        return datetime.now(self._tz)
