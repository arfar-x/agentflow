"""In-memory stand-ins for every port.

The point of the hexagonal split is that the use cases can be proven before any
database or HTTP client exists, so these fakes are the only infrastructure the
application tests need. The store's `search` is a deliberately crude substring
match: it must rank plausibly, not replicate Postgres -- ranking quality is the
Postgres adapter's own test.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Iterator, Sequence

from kb.application.ports.knowledge_source import SourceDocument
from kb.application.ports.summarizer import SummaryDraft
from kb.domain.entry import Entry, EntryType
from kb.domain.merge import Override
from kb.domain.text import normalize, normalize_all


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta) -> None:
        self._now = self._now + delta


class FakeEntryStore:
    def __init__(self) -> None:
        self.entries: dict[str, Entry] = {}
        self.overrides: dict[str, Override] = {}
        self.misses: list[tuple[str, datetime]] = []
        self.upserts = 0
        self.touches = 0

    # -- reads -------------------------------------------------------------
    def get(self, entry_id: str) -> Entry | None:
        return self.entries.get(entry_id)

    def get_many(self, entry_ids: Sequence[str]) -> dict[str, Entry]:
        return {i: self.entries[i] for i in entry_ids if i in self.entries}

    def get_override(self, entry_id: str) -> Override | None:
        return self.overrides.get(entry_id)

    def get_overrides(self, entry_ids: Sequence[str]) -> dict[str, Override]:
        return {i: self.overrides[i] for i in entry_ids if i in self.overrides}

    def ids_for_source(self, source_id: str, *, include_deleted: bool = False) -> set[str]:
        return {
            entry.id
            for entry in self.entries.values()
            if entry.source_id == source_id and (include_deleted or entry.deleted_at is None)
        }

    def search(
        self,
        normalized_query: str,
        *,
        types: Sequence[EntryType] | None = None,
        tags: Sequence[str] | None = None,
        limit: int = 30,
    ) -> list[str]:
        terms = [t for t in normalized_query.split() if t]
        scored: list[tuple[int, str]] = []
        for entry in self.entries.values():
            if entry.deleted_at is not None:
                continue
            if types and entry.type not in types:
                continue
            if tags and not set(tags) & set(entry.tags):
                continue
            haystack = " ".join(
                [
                    normalize_all(entry.title),
                    normalize_all(entry.summary),
                    normalize_all(entry.keywords),
                    normalize_all(entry.aliases),
                    normalize_all(entry.tags),
                ]
            )
            # Title matches count double, the same bias the real adapter's
            # weighting has.
            title = normalize_all(entry.title)
            score = sum(term in haystack for term in terms) + sum(term in title for term in terms)
            if score:
                scored.append((score, entry.id))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [entry_id for _, entry_id in scored[:limit]]

    # -- writes ------------------------------------------------------------
    def upsert(self, entry: Entry) -> None:
        self.entries[entry.id] = entry
        self.upserts += 1

    def touch(self, entry_id: str, *, at: datetime, source_version: str | None = None) -> None:
        entry = self.entries[entry_id]
        self.entries[entry_id] = entry.model_copy(
            update={"last_seen_at": at, "source_version": source_version or entry.source_version}
        )
        self.touches += 1

    def mark_missing(self, entry_ids: Iterable[str], *, at: datetime) -> int:
        changed = 0
        for entry_id in entry_ids:
            entry = self.entries.get(entry_id)
            if entry is None or entry.deleted_at is not None:
                continue
            self.entries[entry_id] = entry.model_copy(update={"deleted_at": at})
            changed += 1
        return changed

    def revive(self, entry_id: str) -> None:
        self.entries[entry_id] = self.entries[entry_id].model_copy(update={"deleted_at": None})

    def log_miss(self, query: str, *, at: datetime) -> None:
        self.misses.append((query, at))

    def set_override(self, override: Override) -> None:
        self.overrides[override.entry_id] = override


class FakeSummarizer:
    """Counts its calls, because "an unchanged sync makes zero model calls" is
    the property most worth protecting."""

    def __init__(self, drafts: dict[str, SummaryDraft] | None = None) -> None:
        self.calls: list[str] = []
        self._drafts = drafts or {}

    def draft(self, document: SourceDocument) -> SummaryDraft:
        self.calls.append(document.external_id)
        if document.external_id in self._drafts:
            return self._drafts[document.external_id]
        return SummaryDraft(
            title={"en": document.title},
            summary={"en": document.body[:120]},
            keywords=tuple(normalize(document.title).split()[:5]),
        )


class FakeKnowledgeSource:
    def __init__(self, source_id: str, documents: Sequence[SourceDocument], checkpoint: str | None = None) -> None:
        self._source_id = source_id
        self._documents = list(documents)
        self._checkpoint = checkpoint

    @property
    def source_id(self) -> str:
        return self._source_id

    def changed_since(self, checkpoint: str | None) -> Iterator[SourceDocument]:
        yield from self._documents

    def list_all(self) -> Iterator[SourceDocument]:
        yield from self._documents

    def fetch(self, external_id: str) -> SourceDocument | None:
        for document in self._documents:
            if document.external_id == external_id:
                return document
        return None

    def checkpoint(self) -> str | None:
        return self._checkpoint
