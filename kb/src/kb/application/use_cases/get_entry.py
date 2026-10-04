"""Read one entry by id.

Separate from search because it answers a different question -- "tell me
everything about this one thing", usually after a search returned its id -- and
because it is the hook the lazy refresh will hang off: reading a stale entry is
the signal that somebody cares about it enough to re-check it (FR-REC-10, phase
7). Until then this reports staleness and leaves the refresh to the scheduler.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from kb.application.ports.clock import Clock
from kb.application.ports.entry_store import EntryStore
from kb.domain.content import CONTENT_PAGE_CHARS, ContentPage, clamp_offset, next_offset
from kb.domain.entry import Entry, fetch_hint_for
from kb.domain.merge import apply_override
from kb.domain.policies import DEFAULT_STALE_AFTER, is_indexable, is_stale

logger = logging.getLogger("kb.get_entry")


class EntryView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: Entry
    stale: bool
    #: True when the entry exists but an override or a soft delete keeps it out
    #: of search. A direct `kb_get` still returns it -- somebody followed a link
    #: to it, and silence would be less useful than "this one is retired".
    hidden: bool = False
    #: One page of the document's own text, for a source that keeps it
    #: (spec §6.10); None for every other entry.
    content: ContentPage | None = None

    @property
    def fetch_hint(self) -> dict[str, Any] | None:
        return fetch_hint_for(
            self.entry.id, self.entry.location, has_content=self.content is not None
        )


class GetEntry:
    def __init__(
        self,
        store: EntryStore,
        clock: Clock,
        *,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
        queue_refresh: bool = True,
    ) -> None:
        self._store = store
        self._clock = clock
        self._stale_after = stale_after
        self._queue_refresh = queue_refresh

    def execute(self, entry_id: str, *, offset: int = 0) -> EntryView | None:
        entry = self._store.get(entry_id)
        if entry is None:
            return None
        override = self._store.get_override(entry_id)
        effective = apply_override(entry, override)
        now = self._clock.now()
        stale = is_stale(effective, now=now, stale_after=self._stale_after)

        if stale and self._queue_refresh:
            # Ask for a re-check rather than doing one: this runs inside the
            # MCP server, which holds a read-only role and no source
            # credentials. The scheduler has both and drains the queue.
            # Failing to queue must never fail the read -- the caller wanted
            # the entry, not the housekeeping.
            try:
                self._store.queue_refresh(entry_id, at=now)
            except Exception:  # noqa: BLE001 - best-effort by design
                logger.warning("could not queue a refresh for %s", entry_id, exc_info=True)

        return EntryView(
            entry=effective,
            stale=stale,
            hidden=not is_indexable(effective, override),
            content=self._content(entry, offset),
        )

    def _content(self, entry: Entry, offset: int) -> ContentPage | None:
        if entry.deleted_at is not None:
            # The source removed it; a copy left over from before is not served
            # (FR-CNT-11), even in the moment before a sync deletes it.
            return None
        start = clamp_offset(offset)
        stored = self._store.read_content(entry.id, offset=start, limit=CONTENT_PAGE_CHARS)
        if stored is None or stored.source_version != entry.source_version:
            # Text from another version than the one the summary describes
            # would answer a different document than the one found. Until the
            # next sync realigns them, the agent reads the source instead.
            return None
        return ContentPage(
            text=stored.text,
            offset=start,
            total_chars=stored.total_chars,
            next_offset=next_offset(start, len(stored.text), stored.total_chars),
            truncated=stored.truncated,
            original_bytes=stored.original_bytes,
        )
