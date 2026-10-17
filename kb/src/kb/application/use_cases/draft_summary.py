"""Ask the model to describe a record -- on request, and only to look at.

What comes back is a *draft*: shown to the operator, edited by them, kept only
when they save (FR-ADM-13). Nothing here writes, and the model's words are
data to display, never instructions (spec C5).
"""

from __future__ import annotations

from typing import Mapping

from kb.application.ports.knowledge_source import SourceDocument
from kb.application.ports.summarizer import Summarizer, SummaryDraft
from kb.domain.entry import EntryType, Location
from kb.sources_config import FORM_SOURCE_ID


class NothingToSummarize(ValueError):
    pass


class DraftSummary:
    def __init__(self, summarizer: Summarizer) -> None:
        self._summarizer = summarizer

    def execute(
        self, *, title: Mapping[str, str], body: str, entry_type: EntryType = EntryType.DOC
    ) -> SummaryDraft:
        text = body.strip()
        if not text:
            raise NothingToSummarize("write the record's text first -- there is nothing to describe yet")
        first = next((t.strip() for t in title.values() if t and t.strip()), "Untitled")
        return self._summarizer.draft(
            SourceDocument(
                source_id=FORM_SOURCE_ID,
                external_id="draft",
                title=first,
                body=text,
                location=Location(kind="form", ref={"record": "draft"}),
                type=entry_type,
            )
        )
