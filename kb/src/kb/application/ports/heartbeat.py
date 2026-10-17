"""The scheduler's pulse, readable by the console (FR-SCH-12).

Holds what only the scheduler can see: whether it is alive, and which credential
variables its environment has. Names and yes/no, never values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class Heartbeat(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    beat_at: datetime
    approved: bool = True
    approved_from: str = "default"
    #: none (no model configured) | ok | failing
    summarizer: str = "none"
    summarizer_error: str | None = None
    #: source id -> variable name -> whether it is set. Never a value.
    credentials: Mapping[str, Mapping[str, bool]] = Field(default_factory=dict)
    #: Why the source configuration could not be read, when it could not.
    config_error: str | None = None
    #: What it is doing right now, when a long job holds it ("full_scrape my-source").
    working_on: str | None = None


@runtime_checkable
class HeartbeatStore(Protocol):
    def beat(self, heartbeat: Heartbeat) -> None: ...

    def last_beat(self) -> Heartbeat | None: ...
