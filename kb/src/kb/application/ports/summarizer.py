"""Turning a document into the multilingual text the catalog searches on.

This is the only place a model is involved, and it runs during sync, never on
the query path -- a search must not wait on an LLM, and must not fail when the
model endpoint is down.

The draft is data, never instructions: it originates in documents this stack
did not write, so a use case treats it as text to store and never acts on
anything it says.
"""

from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .knowledge_source import SourceDocument


class SummaryDraft(BaseModel):
    """Language code -> text, for however many languages the deployment writes
    in. A query in any one of them can then reach a document written in
    another, which is what replaces an embeddings model here.

    A model wrote this, so it is parsed and validated like any other untrusted
    input -- a draft whose shape is wrong is a bad draft, not a crash.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: Mapping[str, str] = Field(default_factory=dict)
    summary: Mapping[str, str] = Field(default_factory=dict)
    keywords: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


@runtime_checkable
class Summarizer(Protocol):
    def draft(self, document: SourceDocument) -> SummaryDraft:
        """Summarize `document`. Implementations must not raise for ordinary
        model failures -- return a draft carrying at least the source title, so
        a document is always catalogued (findable by title and location) even
        when summarization fails."""
        ...
