"""Read one entry by id.

Separate from search because it answers a different question -- "tell me
everything about this one thing", usually after a search returned its id -- and
because it is the hook the lazy refresh will hang off: reading a stale entry is
the signal that somebody cares about it enough to re-check it (FR-REC-10, phase
7). Until then this reports staleness and leaves the refresh to the scheduler.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from kb.application.ports.clock import Clock
from kb.application.ports.entry_store import EntryStore
from kb.domain.entry import Entry
from kb.domain.merge import apply_override
from kb.domain.policies import DEFAULT_STALE_AFTER, is_indexable, is_stale


class EntryView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: Entry
    stale: bool
    #: True when the entry exists but an override or a soft delete keeps it out
    #: of search. A direct `kb_get` still returns it -- somebody followed a link
    #: to it, and silence would be less useful than "this one is retired".
    hidden: bool = False


class GetEntry:
    def __init__(
        self,
        store: EntryStore,
        clock: Clock,
        *,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> None:
        self._store = store
        self._clock = clock
        self._stale_after = stale_after

    def execute(self, entry_id: str) -> EntryView | None:
        entry = self._store.get(entry_id)
        if entry is None:
            return None
        override = self._store.get_override(entry_id)
        effective = apply_override(entry, override)
        return EntryView(
            entry=effective,
            stale=is_stale(effective, now=self._clock.now(), stale_after=self._stale_after),
            hidden=not is_indexable(effective, override),
        )
