"""Re-read the entries readers asked about.

The lazy mechanism, completed: `kb_get` records that somebody opened a stale
entry, and this drains that queue. It is the cheapest freshness there is --
nothing happens until a person cares, and then only the one document is read.

Split across two components on purpose. The MCP server holds a read-only
database role and no source credentials, so it *cannot* refresh anything even by
accident; the scheduler holds both and does the work. The queue is the seam.
"""

from __future__ import annotations

from typing import Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from kb.application.ports.clock import Clock
from kb.application.ports.entry_store import EntryStore
from kb.application.ports.knowledge_source import KnowledgeSource
from kb.application.use_cases.reconcile_document import Action, ReconcileDocument

#: source id -> a live source, built only when one is actually needed. A
#: catalog with ten sources must not construct ten HTTP clients to refresh one
#: page.
SourceResolver = Callable[[str], KnowledgeSource | None]


class RefreshReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    taken: int = 0
    refreshed: int = 0
    unchanged: int = 0
    #: The source no longer has it. Left for the nightly full pass to
    #: soft-delete: a single failed read is not evidence of deletion.
    missing_at_source: int = 0
    failed: tuple[str, ...] = Field(default_factory=tuple)


class RefreshEntries:
    def __init__(
        self,
        store: EntryStore,
        reconcile: ReconcileDocument,
        resolve_source: SourceResolver,
        clock: Clock,
    ) -> None:
        self._store = store
        self._reconcile = reconcile
        self._resolve_source = resolve_source
        self._clock = clock

    def execute(self, *, limit: int = 20) -> RefreshReport:
        entry_ids = self._store.take_refresh_batch(limit=limit)
        if not entry_ids:
            return RefreshReport()

        refreshed = unchanged = missing = 0
        failed: list[str] = []
        sources: dict[str, KnowledgeSource | None] = {}

        for entry_id in entry_ids:
            try:
                outcome = self._refresh_one(entry_id, sources)
            except Exception as exc:  # the source is down, auth expired, ...
                # Left in the queue with its error and a bumped attempt count,
                # so a document that can never be refreshed becomes visible
                # instead of being retried forever.
                failed.append(f"{entry_id}: {exc}")
                self._store.finish_refresh(entry_id, error=str(exc))
                continue

            if outcome is None:
                missing += 1
            elif outcome is Action.UNCHANGED:
                unchanged += 1
            else:
                refreshed += 1
            self._store.finish_refresh(entry_id)

        return RefreshReport(
            taken=len(entry_ids),
            refreshed=refreshed,
            unchanged=unchanged,
            missing_at_source=missing,
            failed=tuple(failed),
        )

    def _refresh_one(
        self, entry_id: str, sources: Mapping[str, KnowledgeSource | None] | dict
    ) -> Action | None:
        entry = self._store.get(entry_id)
        if entry is None or not entry.external_id:
            # Queued before the entry was deleted, or catalogued before
            # entries recorded their source id. Nothing to do; drop it.
            return None

        if entry.source_id not in sources:
            sources[entry.source_id] = self._resolve_source(entry.source_id)
        source = sources[entry.source_id]
        if source is None:
            raise RuntimeError(f"source {entry.source_id!r} is not configured or not enabled")

        document = source.fetch(entry.external_id)
        if document is None:
            return None
        return self._reconcile.execute(document).action
