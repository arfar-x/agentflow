"""Bring the catalog in line with one source.

Everything funnels through `reconcile_document`, so this use case is only about
*which* documents to hand it and what to do with the ones the source no longer
lists.

Two modes, one code path:

- **full** lists everything the source currently has. Only a full pass can
  detect deletions -- an entry the source never yielded is one that is gone --
  so this is the mode that soft-deletes (FR-REC-09).
- **incremental** asks only for what changed since the stored checkpoint. Much
  cheaper, and the backbone of staying current, but it can say nothing about
  deletions: a document that was removed simply never appears. It therefore
  never marks anything missing (FR-REC-08).

Neither mode re-summarizes unchanged content, because `reconcile_document`
decides that by content hash. A full pass over a steady-state source costs API
calls and nothing else.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from kb.application.ports.clock import Clock
from kb.application.ports.entry_store import EntryStore
from kb.application.ports.knowledge_source import KnowledgeSource
from kb.application.use_cases.reconcile_document import Action, ReconcileDocument
from kb.domain.errors import describe_for


class Mode(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class SyncReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    mode: Mode
    dry_run: bool = False
    seen: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    revived: int = 0
    missing: int = 0
    #: How many model calls this run actually made -- the number that makes
    #: "an unchanged sync is free" checkable after the fact rather than claimed.
    summarized: int = 0
    #: Documents that could not become entries, each named with the reason.
    #: A bad page is skipped and reported, never fatal to the run.
    rejected: tuple[str, ...] = Field(default_factory=tuple)
    checkpoint: str | None = None


class SyncSource:
    def __init__(
        self,
        store: EntryStore,
        reconcile: ReconcileDocument,
        clock: Clock,
    ) -> None:
        self._store = store
        self._reconcile = reconcile
        self._clock = clock

    def execute(
        self,
        source: KnowledgeSource,
        *,
        mode: Mode = Mode.FULL,
        dry_run: bool = False,
        checkpoint: str | None = None,
    ) -> SyncReport:
        counts = {action: 0 for action in Action}
        seen: set[str] = set()
        rejected: list[str] = []
        summarized = 0

        documents = (
            source.list_all() if mode is Mode.FULL else source.changed_since(checkpoint)
        )
        for document in documents:
            if dry_run:
                # Still walks the source, so the report says what *would*
                # happen -- the point of a dry run is finding out that a space
                # holds 4,000 pages before summarizing them.
                seen.add(document.external_id)
                continue
            try:
                result = self._reconcile.execute(document)
            except Exception as exc:  # a document that cannot become an entry
                rejected.append(_describe(document, exc))
                continue
            counts[result.action] += 1
            summarized += int(result.summarized)
            seen.add(result.entry_id)

        missing = 0
        if mode is Mode.FULL and not dry_run:
            # Only a full pass knows what is absent. An incremental one never
            # sees an unchanged document, let alone a deleted one.
            known = self._store.ids_for_source(source.source_id)
            missing = self._store.mark_missing(known - seen, at=self._clock.now())

        return SyncReport(
            source_id=source.source_id,
            mode=mode,
            dry_run=dry_run,
            seen=len(seen),
            created=counts[Action.CREATED],
            updated=counts[Action.UPDATED],
            unchanged=counts[Action.UNCHANGED],
            revived=counts[Action.REVIVED],
            missing=missing,
            summarized=summarized,
            rejected=tuple(rejected),
            checkpoint=None if dry_run else source.checkpoint(),
        )


def _describe(document, exc: Exception) -> str:
    from pydantic import ValidationError

    subject = f"{document.source_id}:{document.external_id}"
    if isinstance(exc, ValidationError):
        return describe_for(subject, exc)
    return f"{subject}: {exc}"
