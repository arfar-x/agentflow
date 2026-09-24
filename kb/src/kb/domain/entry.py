"""What a catalog entry is.

An entry describes a document; it never contains one. That is the point of the
design: `location` tells an agent where the real content lives so it can go and
read it, rather than answering from a summary (see
`docs/spec/knowledge-base.md`, Part 2).

Modelled with pydantic so **an invalid entry cannot exist**: every path that
builds one -- a source adapter, a database row, a JSON payload -- goes through
the same validation, and a malformed document is rejected where it is built
rather than discovered later in search results. Pydantic also reports every
problem in one error, so one bad source produces one complete report instead of
one field per sync run.

Localized fields (`title`, `summary`) are plain language-code -> text maps
rather than fixed `en`/`fa` attributes, so a deployment can catalog whatever
languages it writes in without a schema change.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EntryType(str, Enum):
    DOC = "doc"
    SPEC = "spec"
    PRODUCT = "product"
    SYSTEM = "system"
    TEAM = "team"
    PERSON = "person"
    TERM = "term"


class EntryStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class Location(BaseModel):
    """Where the real document lives.

    `kind` names the system ("confluence", "gitlab", "jira", "http_api",
    "local"); `ref` holds whatever that system needs to fetch it again (a page
    id, a project path plus file path, an issue key); `url` is for a human to
    click.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(min_length=1)
    ref: Mapping[str, str] = Field(default_factory=dict)
    url: str | None = None


#: How to re-read a document, per source system. The tool names are the
#: toolset's own (`confluence_get_page`), not the suffixed names LibreChat
#: exposes (`confluence_get_page_mcp_agent-skills`) -- the agent instructions
#: cover that mapping, and hardcoding one client's suffix here would tie the
#: catalog to LibreChat.
FETCH_TOOLS: Mapping[str, tuple[str, str]] = {
    "confluence": ("confluence_get_page", "page_id"),
    "jira": ("jira_issue_summary", "issue_key"),
}


def fetch_hint(location: Location) -> dict[str, Any] | None:
    """The ready-made next call for reading this document, or None when the
    source has no tool in this stack and the agent should use `url` instead.

    This exists so a smaller model doesn't have to work out which tool fetches
    a Confluence page versus a Jira issue -- getting that wrong is the most
    likely way a two-step retrieval fails.
    """
    mapped = FETCH_TOOLS.get(location.kind)
    if mapped is None:
        return None
    tool, arg = mapped
    value = location.ref.get(arg)
    if not value:
        return None
    return {"tool": tool, "args": {arg: value}}


class Entry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)

    id: str = Field(min_length=1)
    type: EntryType
    title: Mapping[str, str]
    location: Location
    #: Which configured source produced this entry. Nightly reconciliation asks
    #: for every entry of one source to find the ones that vanished, so an
    #: entry without it could never be detected as deleted.
    source_id: str = Field(min_length=1)
    #: The id that source knows the document by (a page id, an issue key,
    #: `project:path`). The entry id is a slug, and slugs do not invert, so
    #: refreshing one entry on its own needs the original kept here.
    external_id: str | None = None
    summary: Mapping[str, str] = Field(default_factory=dict)
    keywords: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    owner_team: str | None = None
    #: Recorded from day one, enforced in a later version (spec, Part 2).
    audience: tuple[str, ...] = ("all",)
    status: EntryStatus | None = None
    #: The source's own version marker (Confluence version number, git SHA,
    #: Jira updated timestamp) -- what staleness is judged against.
    source_version: str | None = None
    #: Hash of the source content the summary was generated from. Equal hash
    #: means no model call and no write; this is what makes sync cheap.
    content_hash: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source_created_at: datetime | None = None
    source_updated_at: datetime | None = None
    last_seen_at: datetime | None = None
    #: Set instead of deleting the row, so a source that disappears because of
    #: a permission change or an outage can come back without losing history.
    deleted_at: datetime | None = None

    @field_validator("id", "source_id")
    @classmethod
    def _no_whitespace(cls, value: str) -> str:
        if value != value.strip() or any(char.isspace() for char in value):
            raise ValueError("must not contain whitespace")
        return value

    @field_validator("title")
    @classmethod
    def _title_has_text(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        if not any(text and text.strip() for text in value.values()):
            raise ValueError("needs non-empty text in at least one language")
        return value

    @field_validator("audience")
    @classmethod
    def _audience_not_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("must list at least one audience (use ('all',) for unrestricted)")
        return value

    @model_validator(mode="after")
    def _document_must_be_reachable(self) -> "Entry":
        """An entry that can be found but never read is the one outcome the
        catalog exists to prevent: the agent is told the answer exists and has
        no way to reach it."""
        mapped = FETCH_TOOLS.get(self.location.kind)
        if mapped is not None:
            _, required = mapped
            if not self.location.ref.get(required):
                raise ValueError(
                    f"location.ref is missing {required!r}, so the document could never be fetched"
                )
        elif not self.location.url:
            raise ValueError("location has neither a fetchable ref nor a url")
        return self

    @property
    def fetch_hint(self) -> dict[str, Any] | None:
        return fetch_hint(self.location)
