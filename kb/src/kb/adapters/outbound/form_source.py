"""The `form` source: manual records, read through the same port as any wiki.

There is no second path into the catalog for them. A saved record is just a
source whose documents happen to live in our own database, and they go through
`ReconcileDocument` like everything else -- so the content-hash gate, soft
delete and raw-text storage all apply unchanged (FR-SRC-07).
"""

from __future__ import annotations

from typing import Iterator

from kb.application.ports.form_records import FormRecord, FormRecords
from kb.application.ports.knowledge_source import SourceDocument
from kb.application.ports.summarizer import Summarizer, SummaryDraft
from kb.domain.entry import Location
from kb.sources_config import FORM_SOURCE_ID

#: Where the operator's approved wording travels, on the document.
DRAFT_KEY = "draft"


class FormKnowledgeSource:
    """Implements `kb.application.ports.knowledge_source.KnowledgeSource`."""

    def __init__(self, records: FormRecords) -> None:
        self._records = records

    @property
    def source_id(self) -> str:
        return FORM_SOURCE_ID

    def list_all(self) -> Iterator[SourceDocument]:
        for record in self._records.list():
            yield to_document(record)

    def changed_since(self, checkpoint: str | None) -> Iterator[SourceDocument]:
        # A table of a few dozen rows: the content-hash gate makes a full pass
        # as cheap as an incremental one, and there is no cursor to get wrong.
        return self.list_all()

    def fetch(self, external_id: str) -> SourceDocument | None:
        record = self._records.get(external_id)
        return to_document(record) if record else None

    def checkpoint(self) -> str | None:
        return None


def to_document(record: FormRecord) -> SourceDocument:
    draft = SummaryDraft(
        title=dict(record.title),
        summary=dict(record.summary),
        keywords=record.keywords,
        aliases=record.aliases,
    )
    return SourceDocument(
        source_id=FORM_SOURCE_ID,
        external_id=record.slug,
        title=record.display_title,
        body=record.body,
        location=Location(kind="form", ref={"record": record.slug}),
        type=record.type,
        # The revision, not a timestamp: it moves on every save, including one
        # that only edits the summary, and the content hash includes it
        # (FR-REC-02) -- that is what makes such a save publish.
        version=str(record.revision),
        created_at=record.created_at,
        updated_at=record.updated_at,
        tags=record.tags,
        owner_team=record.owner_team,
        extra={DRAFT_KEY: draft.model_dump_json()},
    )


class PresetSummarizer:
    """Uses the wording an operator already approved, and asks the model only
    for documents that carry none.

    A record is never summarized behind its author's back: the model runs when
    they press "generate", and what they keep is what is published (FR-ADM-13).
    Everything else a source yields is delegated untouched.
    """

    def __init__(self, inner: Summarizer) -> None:
        self._inner = inner

    def draft(self, document: SourceDocument) -> SummaryDraft:
        raw = document.extra.get(DRAFT_KEY)
        if document.source_id == FORM_SOURCE_ID and raw is not None:
            return SummaryDraft.model_validate_json(raw)
        return self._inner.draft(document)

    def __getattr__(self, name: str):
        # `check()` and anything else the real summarizer offers.
        return getattr(self._inner, name)
