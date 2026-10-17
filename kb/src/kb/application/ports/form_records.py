"""Manual records: knowledge an operator writes into the catalog itself.

For what has no home anywhere else -- a decision nobody wrote down, a team's
glossary. They are the one place the catalog holds an author's own words
(spec N2), and they reach it through the same funnel as every other source
(`FormKnowledgeSource`, FR-SRC-07), not through a side door.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kb.domain.entry import EntryType

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]$|^[a-z0-9]$")
#: `en`, `fa`, `pt-BR` -- and `und` for "no language".
LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


class FormRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    #: Per language, like every entry's. At least one must have text (FR-ENT-03).
    title: Mapping[str, str]
    body: str = Field(min_length=1)
    type: EntryType = EntryType.DOC
    #: What the entry says about itself, per language -- written or approved by
    #: the operator, never generated behind their back.
    summary: Mapping[str, str] = Field(default_factory=dict)
    tags: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    owner_team: str | None = None
    #: Moves on every save, summary-only edits included: it is the record's
    #: version, and the content-hash gate publishes a change only if it moves.
    revision: int = Field(default=1, ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG.match(value):
            raise ValueError("use lowercase letters, digits and '-' (at most 80 characters)")
        return value

    @field_validator("title", "summary")
    @classmethod
    def _languages(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        for language in value:
            if language != "und" and not LANGUAGE.match(language):
                raise ValueError(f"{language!r} is not a language code like 'en' or 'fa'")
        return {k: v.strip() for k, v in value.items() if v and v.strip()}

    @field_validator("title")
    @classmethod
    def _titled(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        if not value:
            raise ValueError("needs a title in at least one language")
        return value

    @field_validator("tags", "keywords", "aliases")
    @classmethod
    def _tidy(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for item in value:
            if item and item.strip():
                seen.setdefault(item.strip(), None)
        return tuple(seen)

    @property
    def display_title(self) -> str:
        """One line for a table, a log or a model's prompt."""
        return next(iter(self.title.values()))


@runtime_checkable
class FormRecords(Protocol):
    def list(self) -> Sequence[FormRecord]: ...

    def get(self, slug: str) -> FormRecord | None: ...

    def create(self, record: FormRecord, *, at: datetime) -> FormRecord: ...

    def update(self, record: FormRecord, *, at: datetime) -> FormRecord:
        """Replace the record and move its revision on by one."""
        ...

    def delete(self, slug: str) -> bool: ...
