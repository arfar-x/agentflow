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

from kb.domain.entry import Entry, EntryType
from kb.domain.merge import Override


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
        rows changed."""
        ...

    def revive(self, entry_id: str) -> None:
        """Clear a soft delete, for a document that came back."""
        ...

    def ids_for_source(self, source_id: str, *, include_deleted: bool = False) -> set[str]:
        """Every entry this source produced -- what reconciliation compares
        against to find deletions."""
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
