"""The boundary types: what crosses into the application, and what the fakes
promise about the real adapters that will replace them."""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from kb.application.ports import (
    Clock,
    EntryStore,
    KnowledgeSource,
    SourceDocument,
    Summarizer,
    SummaryDraft,
)
from kb.application.use_cases.reconcile_document import ReconcileDocument
from kb.application.use_cases.search_catalog import SearchCatalog
from kb.domain.entry import Location
from tests.application.fakes import (
    FakeClock,
    FakeEntryStore,
    FakeKnowledgeSource,
    FakeSummarizer,
)


def test_a_source_document_missing_its_identity_is_rejected_at_the_boundary():
    # Covers: NFR-ARC-03
    # A source whose API shape drifted fails here, naming the field, rather
    # than three layers down with an entry that cannot be fetched.
    with pytest.raises(ValidationError):
        SourceDocument(source_id="", external_id="1", title="t", location=Location(kind="url", url="u"))
    with pytest.raises(ValidationError):
        SourceDocument(source_id="s", external_id="", title="t", location=Location(kind="url", url="u"))


def test_a_model_draft_is_parsed_not_trusted():
    # Covers: NFR-ARC-03
    # The draft comes from a model reading documents this stack did not write:
    # it is validated like any other untrusted input.
    assert SummaryDraft().title == {}
    with pytest.raises(ValidationError):
        SummaryDraft(title={"en": "ok"}, instructions="ignore previous")


def test_the_fakes_satisfy_the_ports_the_adapters_will_implement():
    # Covers: NFR-ARC-04
    assert isinstance(FakeEntryStore(), EntryStore)
    assert isinstance(FakeSummarizer(), Summarizer)
    assert isinstance(FakeClock(__import__("datetime").datetime.now()), Clock)
    assert isinstance(FakeKnowledgeSource("s", []), KnowledgeSource)


def test_search_depends_on_nothing_that_can_be_slow_or_down():
    # Covers: FR-SRCH-09, NFR-PRF-02
    # A search must keep working when the model endpoint is down, so the use
    # case has no summarizer, embedder or source among its dependencies.
    parameters = set(inspect.signature(SearchCatalog.__init__).parameters)
    assert parameters == {"self", "store", "clock", "default_limit", "stale_after"}
    # …while reconciliation, which may be slow, is where the model lives.
    assert "summarizer" in inspect.signature(ReconcileDocument.__init__).parameters
