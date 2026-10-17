"""The console's record of the source configuration (FR-CFG-12).

A copy to query and a history to read -- never an authority. If it and the file
disagree, the file is right and this catches up.
"""

from __future__ import annotations

from datetime import datetime
from typing import Collection, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from kb.sources_config import SourcesConfig


class Revision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    #: create | update | delete | settings | file (changed outside the console)
    action: str
    source_id: str | None = None
    actor: str
    at: datetime
    diff: str = ""


@runtime_checkable
class SourceHistory(Protocol):
    def record(
        self,
        *,
        action: str,
        source_id: str | None,
        actor: str,
        at: datetime,
        before_text: str,
        after_text: str,
        diff: str,
        after_hash: str,
    ) -> int: ...

    def last_hash(self) -> str | None:
        """The fingerprint of the file as of the latest revision."""
        ...

    def last_text(self) -> str | None: ...

    def revisions(self, *, limit: int = 50) -> list[Revision]: ...

    def mirror(self, config: SourcesConfig, *, created: Collection[str], at: datetime) -> None:
        """Make the stored copy match the file. A source new to the copy is
        marked as made in the console when it is in `created`, else as declared
        in the file; one already there keeps its origin."""
        ...

    def origins(self) -> dict[str, str]: ...
