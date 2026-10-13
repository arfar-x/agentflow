"""Where entries are kept and searched.

Postgres implements this today. The protocol is written so an engine with its
own lexical analyzers could implement it instead without any use case
changing: `search` takes an already-normalized query and returns ranked ids,
leaving *how* matching works to the adapter and *how results combine* to the
domain's fusion.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from kb.domain.content import RawContent
from kb.domain.entry import Entry, EntryType
from kb.domain.merge import Override


class ContentSlice(BaseModel):
    """One stretch of an entry's stored text, as the store hands it back.

    A slice rather than the whole text so the query path never moves a 10 MB
    document to return one page of it; where the next page starts is the
    domain's arithmetic (`domain.content.next_offset`), not the store's.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    offset: int = Field(ge=0)
    total_chars: int = Field(ge=0)
    truncated: bool = False
    original_bytes: int = Field(ge=0)
    source_version: str | None = None


@runtime_checkable
class EntryStore(Protocol):
    def get(self, entry_id: str) -> Entry | None: ...

    def get_many(self, entry_ids: Sequence[str]) -> dict[str, Entry]: ...

    def get_override(self, entry_id: str) -> Override | None: ...

    def get_overrides(self, entry_ids: Sequence[str]) -> dict[str, Override]: ...

    def upsert(self, entry: Entry) -> None:
        """Write the entry and rebuild its searchable row."""
        ...

    def touch(self, entry_id: str, *, at: datetime, source_version: str | None = None) -> None:
        """Record that the source was checked and matched, without rewriting
        the entry. This is the unchanged-document path: it must not bump
        `updated_at`, or every sync would look like a change."""
        ...

    def mark_missing(self, entry_ids: Iterable[str], *, at: datetime) -> int:
        """Soft-delete: the source no longer lists these. Returns how many
        rows changed. Their stored text goes too (FR-CNT-06): the entry stays
        for history, but the catalog has no business keeping a copy of a
        document its source has removed."""
        ...

    def revive(self, entry_id: str) -> None:
        """Clear a soft delete, for a document that came back."""
        ...

    def ids_for_source(self, source_id: str, *, include_deleted: bool = False) -> set[str]:
        """Every entry this source produced -- what reconciliation compares
        against to find deletions."""
        ...

    # -- raw content (spec §6.10) -----------------------------------------
    def put_content(self, entry_id: str, content: RawContent, *, source_version: str | None) -> bool:
        """Keep this text for the entry, read at `source_version`. Returns
        whether anything was written: re-storing identical text is a no-op, so
        a nightly full pass over an unchanged source rewrites nothing."""
        ...

    def delete_content(self, entry_id: str) -> None:
        """Drop one entry's text, if it has any."""
        ...

    def purge_content(self, source_id: str) -> int:
        """Drop every text kept for this source's entries -- what turning
        `store_raw_content` off means (FR-CNT-05). Returns how many."""
        ...

    def read_content(self, entry_id: str, *, offset: int, limit: int) -> ContentSlice | None:
        """Up to `limit` characters of the entry's text from `offset`, or None
        when it has none. An offset past the end is an empty slice, not None."""
        ...

    def content_ids(self, entry_ids: Sequence[str]) -> set[str]:
        """Which of these entries have text kept -- one question for a whole
        page of search hits, not one per hit."""
        ...

    def search(
        self,
        normalized_query: str,
        *,
        types: Sequence[EntryType] | None = None,
        tags: Sequence[str] | None = None,
        limit: int = 30,
    ) -> list[str]:
        """Ranked entry ids for one already-normalized query, best first.

        One query at a time on purpose: combining several is the domain's job
        (`ranking.reciprocal_rank_fusion`), so it stays testable without a
        database and identical across storage engines.
        """
        ...

    def log_miss(self, query: str, *, at: datetime) -> None:
        """Record a search that found nothing -- the ranked list of what the
        organization hasn't written down."""
        ...

    def set_override(self, override: Override) -> None: ...

    def queue_refresh(self, entry_id: str, *, at: datetime) -> None:
        """Record that a reader found this entry stale. Idempotent: many
        readers asking about one entry is still one refresh."""
        ...

    def take_refresh_batch(self, *, limit: int = 20, max_attempts: int = 5) -> list[str]:
        """Claim queued entries for refreshing, oldest first."""
        ...

    def finish_refresh(self, entry_id: str, *, error: str | None = None) -> None: ...
