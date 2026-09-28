"""Search the catalog.

Takes several query strings, because the agent sends the user's wording and the
same terms in another language: documents are written in more than one, and one
phrasing must be able to reach the other. Each query is searched separately and
the rankings are fused, so a result found by both rises above one found by
either.

Never calls a model and never touches the network: a search is a database query
plus pure functions, and stays working when the model endpoint is down.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict

from kb.application.ports.clock import Clock
from kb.application.ports.entry_store import EntryStore
from kb.domain.entry import Entry, EntryType
from kb.domain.merge import apply_override
from kb.domain.policies import DEFAULT_STALE_AFTER, is_indexable, is_stale
from kb.domain.ranking import reciprocal_rank_fusion
from kb.domain.text import normalize

#: How many candidates to pull per query before fusing. Wider than the caller's
#: limit on purpose: an entry ranked 9th by one query and 2nd by another should
#: still be able to win, which it can't if each list was already truncated.
CANDIDATE_MULTIPLIER = 4
MIN_CANDIDATES = 30
MAX_QUERY_CHARS = 500


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: Entry
    score: float
    stale: bool

    @property
    def fetch_hint(self) -> dict[str, Any] | None:
        return self.entry.fetch_hint


class SearchResult(BaseModel):
    """The MCP tool serializes this straight to its caller, which is why it is
    a model rather than a tuple: one schema, validated, for the CLI and the
    tool alike."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hits: tuple[SearchHit, ...] = ()
    #: The normalized forms actually searched, so a caller can see what a
    #: query became -- the first thing to check when a search surprises you.
    queries: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class SearchCatalog:
    def __init__(
        self,
        store: EntryStore,
        clock: Clock,
        *,
        default_limit: int = 8,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> None:
        self._store = store
        self._clock = clock
        self._default_limit = default_limit
        self._stale_after = stale_after

    def execute(
        self,
        queries: Sequence[str],
        *,
        types: Sequence[EntryType] | None = None,
        tags: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> SearchResult:
        limit = limit or self._default_limit
        now = self._clock.now()

        # The query comes from a model, so it is bounded before it reaches the
        # store rather than trusted to be sane.
        normalized = []
        for query in queries:
            cleaned = normalize(query or "")[:MAX_QUERY_CHARS]
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        if not normalized:
            return SearchResult(notes=("no searchable terms in the query",))

        candidates = max(MIN_CANDIDATES, limit * CANDIDATE_MULTIPLIER)
        rankings = [
            self._store.search(query, types=types, tags=tags, limit=candidates)
            for query in normalized
        ]
        fused = reciprocal_rank_fusion(rankings)
        if not fused:
            self._store.log_miss(" | ".join(normalized), at=now)
            return SearchResult(queries=tuple(normalized), notes=("nothing in the catalog matched",))

        # Over-fetch before filtering: an excluded or soft-deleted entry must
        # not consume one of the caller's result slots.
        ordered_ids = [entry_id for entry_id, _ in fused][: limit * 2]
        entries = self._store.get_many(ordered_ids)
        overrides = self._store.get_overrides(ordered_ids)
        scores = dict(fused)

        hits: list[SearchHit] = []
        for entry_id in ordered_ids:
            entry = entries.get(entry_id)
            if entry is None:
                continue  # indexed row without its entry: a repair job's problem, not a caller's
            override = overrides.get(entry_id)
            if not is_indexable(entry, override):
                continue
            effective = apply_override(entry, override)
            hits.append(
                SearchHit(
                    entry=effective,
                    score=scores[entry_id],
                    stale=is_stale(effective, now=now, stale_after=self._stale_after),
                )
            )
            if len(hits) == limit:
                break

        if not hits:
            self._store.log_miss(" | ".join(normalized), at=now)
            return SearchResult(queries=tuple(normalized), notes=("nothing in the catalog matched",))

        return SearchResult(hits=tuple(hits), queries=tuple(normalized))
