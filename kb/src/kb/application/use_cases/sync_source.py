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
calls and nothing else -- unless `force=True` (FR-REC-12), which overrides
that gate for every document the run sees. It exists for one situation: a
summarizer defect (a bad prompt, an endpoint that ignored structured-output
hints) has already been fixed, but the documents it degraded have unchanged
content and so would never be picked up by an ordinary run. It costs a model
call per document regardless of whether anything actually changed, same as a
first run.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from kb.application.ports.clock import Clock
from kb.application.ports.entry_store import EntryStore
from kb.application.ports.knowledge_source import KnowledgeSource, SourceDocument
from kb.application.use_cases.reconcile_document import Action, ReconcileDocument
from kb.domain.content import ContentPolicy
from kb.domain.errors import describe_for


class Mode(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class SyncReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    mode: Mode
    dry_run: bool = False
    force: bool = False
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
    #: Documents whose text was written this run (new or changed), for a
    #: source that keeps text.
    content_stored: int = 0
    #: Texts removed because the source no longer keeps text (FR-CNT-05).
    content_purged: int = 0
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
        force: bool = False,
        checkpoint: str | None = None,
        on_progress: Callable[[int, SourceDocument], None] | None = None,
        content: ContentPolicy | None = None,
    ) -> SyncReport:
        content = content or ContentPolicy.off()
        counts = {action: 0 for action in Action}
        seen: set[str] = set()
        rejected: list[str] = []
        summarized = 0
        content_stored = 0

        documents = (
            source.list_all() if mode is Mode.FULL else source.changed_since(checkpoint)
        )
        for index, document in enumerate(documents, start=1):
            if on_progress is not None:
                # A plain callback, not an import of an I/O module -- the
                # boundary rule (test_boundaries.py) is about what this layer
                # imports, not about a caller handing it a function. The
                # actual printing lives in the adapter that constructs this
                # use case (cli.py), same as every other side effect here.
                on_progress(index, document)
            if dry_run:
                # Still walks the source, so the report says what *would*
                # happen -- the point of a dry run is finding out that a space
                # holds 4,000 pages before summarizing them.
                seen.add(document.external_id)
                continue
            try:
                result = self._reconcile.execute(document, force=force, content=content)
            except Exception as exc:  # a document that cannot become an entry
                rejected.append(_describe(document, exc))
                continue
            counts[result.action] += 1
            summarized += int(result.summarized)
            content_stored += int(result.content_stored)
            seen.add(result.entry_id)

        missing = 0
        if mode is Mode.FULL and not dry_run:
            # Only a full pass knows what is absent. An incremental one never
            # sees an unchanged document, let alone a deleted one.
            known = self._store.ids_for_source(source.source_id)
            missing = self._store.mark_missing(known - seen, at=self._clock.now())

        content_purged = 0
        if not content.store and not dry_run:
            # A source that does not keep text has none kept -- including text
            # from before somebody turned the flag off. Any mode will do: this
            # is about the source's setting, not about which documents changed,
            # so revoking takes effect on the next incremental tick (FR-CNT-05).
            content_purged = self._store.purge_content(source.source_id)

        return SyncReport(
            source_id=source.source_id,
            mode=mode,
            dry_run=dry_run,
            force=force,
            seen=len(seen),
            created=counts[Action.CREATED],
            updated=counts[Action.UPDATED],
            unchanged=counts[Action.UNCHANGED],
            revived=counts[Action.REVIVED],
            missing=missing,
            summarized=summarized,
            rejected=tuple(rejected),
            content_stored=content_stored,
            content_purged=content_purged,
            checkpoint=None if dry_run else source.checkpoint(),
        )


def _describe(document, exc: Exception) -> str:
    from pydantic import ValidationError

    subject = f"{document.source_id}:{document.external_id}"
    if isinstance(exc, ValidationError):
        return describe_for(subject, exc)
    return f"{subject}: {exc}"
