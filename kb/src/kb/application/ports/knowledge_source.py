"""A place knowledge is written: a wiki space, a set of repository paths, an
issue tracker query, an HTTP endpoint.

Every source answers the same three questions -- what exists, what changed
since a checkpoint, and what does one item contain -- so the use cases never
know which system they're talking to. Adding a source is implementing this
protocol; nothing in `domain/` or `use_cases/` changes (spec, Part 6).
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterator, Mapping, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from kb.domain.entry import EntryType, Location


class SourceDocument(BaseModel):
    """One document as its source describes it, before the catalog has an
    opinion about it.

    Validated, because this is where data from outside crosses into the
    application: a wiki that returns a page with no id, or an API whose shape
    drifted, fails here with a readable message rather than three layers down.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    #: The id in that source: a page id, a repository file path, an issue key.
    external_id: str = Field(min_length=1)
    title: str
    body: str = ""
    location: Location
    type: EntryType = EntryType.DOC
    #: The source's own change marker: version number, commit SHA, timestamp.
    version: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tags: tuple[str, ...] = ()
    owner_team: str | None = None
    #: Anything source-specific a summarizer or adapter may want to keep
    #: (labels, space key, project path). Not indexed.
    extra: Mapping[str, str] = Field(default_factory=dict)


@runtime_checkable
class KnowledgeSource(Protocol):
    """Read-only access to one configured source."""

    @property
    def source_id(self) -> str: ...

    def changed_since(self, checkpoint: str | None) -> Iterator[SourceDocument]:
        """Documents changed since `checkpoint` (None means everything).

        The checkpoint is opaque to the caller -- a timestamp for a wiki, a
        commit SHA for a repository -- because only the source knows what its
        own cursor means.
        """
        ...

    def list_all(self) -> Iterator[SourceDocument]:
        """Every document this source currently exposes.

        Used by nightly reconciliation, which is the only mechanism that can
        detect a deletion: an entry this never yields no longer exists.
        """
        ...

    def fetch(self, external_id: str) -> SourceDocument | None:
        """One document, for the lazy refresh of a single stale entry."""
        ...

    def checkpoint(self) -> str | None:
        """The cursor to store after a successful run."""
        ...
