"""The source configuration as a file the console may edit, and the vocabulary
of an edit.

The file stays the one source of truth (FR-CFG-12): the scheduler reads only it.
What the console adds is a careful way to change it -- see `EditSources`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol, Union, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from kb.sources_config import SourcesConfig


class FileSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    #: A fingerprint of the exact bytes. An apply that carries the preview's
    #: etag is refused when the file is no longer what the preview described.
    etag: str


class FileChanged(RuntimeError):
    """The file is not what it was when the change was previewed."""


class FileNotWritable(RuntimeError):
    """The console cannot write the file (a read-only mount, or a host-owned
    file its user may not change)."""


class CreateSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["create"] = "create"
    config: dict[str, Any]


class UpdateSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["update"] = "update"
    id: str = Field(min_length=1)
    #: The source's whole desired settings, as the edit form holds them.
    config: dict[str, Any]


class DeleteSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["delete"] = "delete"
    id: str = Field(min_length=1)


class SetApproved(BaseModel):
    """The global switch (FR-CFG-05)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["approved"] = "approved"
    value: bool


SourceChange = Annotated[
    Union[CreateSource, UpdateSource, DeleteSource, SetApproved], Field(discriminator="action")
]


@runtime_checkable
class SourcesFile(Protocol):
    def snapshot(self) -> FileSnapshot: ...

    def write(self, text: str, *, expected_etag: str) -> FileSnapshot:
        """Replace the file's contents if it is still `expected_etag`; raises
        `FileChanged` if not, `FileNotWritable` if it cannot be written."""
        ...

    def writable(self) -> bool: ...


@runtime_checkable
class SourceTextEditor(Protocol):
    def apply(self, text: str, change: "CreateSource | UpdateSource | DeleteSource | SetApproved", current: SourcesConfig) -> str:
        """The file's text with `change` made to it, and nothing else touched:
        comments, ordering and `${VAR}` placeholders stay as written."""
        ...
