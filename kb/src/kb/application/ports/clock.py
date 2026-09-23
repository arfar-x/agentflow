"""Time, injected.

Staleness, soft deletion and "last seen" all compare timestamps, so tests need
to control now() rather than sleep.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """UTC, always -- entries are compared across a stack whose containers
    need not agree on a local timezone."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
