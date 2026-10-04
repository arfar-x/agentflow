"""Reconcile one source document against the catalog.

**This is the single funnel.** Incremental sync, nightly reconciliation, the
lazy refresh and the webhook receiver all call this and nothing else, so the
four mechanisms cannot drift into four subtly different definitions of what an
entry should look like (spec, Part 3).

The expensive step -- summarization -- happens only when the document's content
hash has actually changed. Everything else is a timestamp update.

For a source that keeps its documents' text (`store_raw_content`, spec §6.10)
the text is written here too, after the entry, on *every* path including the
unchanged one. That is what lets a source switched on after it was catalogued
fill its text on the next pass without a single model call (FR-CNT-04).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

from kb.application.ports.clock import Clock
from kb.application.ports.entry_store import EntryStore
from kb.application.ports.knowledge_source import SourceDocument
from kb.application.ports.summarizer import Summarizer
from kb.domain.content import ContentPolicy, prepare
from kb.domain.entry import Entry
from kb.domain.identity import content_hash, entry_id_for
from kb.domain.policies import needs_resummarize


class Action(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    REVIVED = "revived"


class ReconcileResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str
    action: Action
    #: Whether a model call was made. Summing this over a run is how the
    #: "unchanged sources cost nothing" claim is verified.
    summarized: bool
    #: Whether the document's text was written (new, or changed since last time).
    content_stored: bool = False


class ReconcileDocument:
    def __init__(self, store: EntryStore, summarizer: Summarizer, clock: Clock) -> None:
        self._store = store
        self._summarizer = summarizer
        self._clock = clock

    def execute(
        self,
        document: SourceDocument,
        *,
        force: bool = False,
        content: ContentPolicy | None = None,
    ) -> ReconcileResult:
        """`force` bypasses the content-hash gate below: FR-REC-12's escape
        hatch for recovering entries a fixed summarizer defect left
        undescribed, whose content never actually changed and so would
        otherwise never be re-summarized on its own.

        `content` is the source's decision about keeping text; absent means no.
        Not purging here when it is off is deliberate -- turning a source off
        is a whole-source decision, and `SyncSource` acts on it once per run."""
        result = self._reconcile(document, force=force)
        if content is None or not content.store:
            return result
        return result.model_copy(update={"content_stored": self._keep_text(document, content)})

    def _keep_text(self, document: SourceDocument, policy: ContentPolicy) -> bool:
        entry_id = entry_id_for(document.source_id, document.external_id)
        text = prepare(document.body, policy.max_bytes)
        if text is None:
            # Nothing to read is not a document with empty text; an old copy
            # from before the body went away must not be served either.
            self._store.delete_content(entry_id)
            return False
        return self._store.put_content(entry_id, text, source_version=document.version)

    def _reconcile(self, document: SourceDocument, *, force: bool) -> ReconcileResult:
        entry_id = entry_id_for(document.source_id, document.external_id)
        existing = self._store.get(entry_id)
        digest = content_hash(document.title, document.body, document.version)
        now = self._clock.now()

        if not force and not needs_resummarize(existing, digest):
            # The document is byte-identical to what we already catalogued.
            # No model call, no rewrite -- only "we checked, it's still here",
            # which is what keeps a 15-minute cadence affordable.
            assert existing is not None  # needs_resummarize(None, ...) is always True
            if existing.deleted_at is not None:
                self._store.revive(entry_id)
                self._store.touch(entry_id, at=now, source_version=document.version)
                return ReconcileResult(entry_id=entry_id, action=Action.REVIVED, summarized=False)
            self._store.touch(entry_id, at=now, source_version=document.version)
            return ReconcileResult(entry_id=entry_id, action=Action.UNCHANGED, summarized=False)

        draft = self._summarizer.draft(document)
        # Entry's own validation runs here: a source document that cannot make
        # a reachable entry raises now, naming the field, instead of becoming a
        # search result the agent can never follow.
        entry = Entry(
            id=entry_id,
            type=document.type,
            # A model that returned nothing usable still leaves the document
            # catalogued under its real title -- degraded, not lost.
            title=draft.title or {"und": document.title},
            location=document.location,
            source_id=document.source_id,
            external_id=document.external_id,
            summary=draft.summary,
            keywords=draft.keywords,
            tags=document.tags,
            aliases=draft.aliases,
            owner_team=document.owner_team,
            source_version=document.version,
            content_hash=digest,
            # `created_at` records when the catalog first learned of the
            # document, so a re-summarized entry keeps its original.
            created_at=existing.created_at if existing else now,
            updated_at=now,
            source_created_at=document.created_at,
            source_updated_at=document.updated_at,
            last_seen_at=now,
            deleted_at=None,
        )
        self._store.upsert(entry)
        action = (
            Action.REVIVED
            if existing and existing.deleted_at
            else (Action.UPDATED if existing else Action.CREATED)
        )
        return ReconcileResult(entry_id=entry_id, action=action, summarized=True)
