"""Keeping a document's text, through the one write funnel."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kb.application.ports.knowledge_source import SourceDocument
from kb.application.use_cases.reconcile_document import Action, ReconcileDocument
from kb.domain.content import ContentPolicy
from kb.domain.entry import EntryType, Location
from tests.application.fakes import FakeClock, FakeEntryStore, FakeSummarizer

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
KEEP = ContentPolicy(store=True)


def adr(*, body: str = "# ADR-7\n\nWe chose Postgres over Mongo.", version: str = "sha-1") -> SourceDocument:
    return SourceDocument(
        source_id="gitlab-adrs",
        external_id="platform/specs:docs/adr-007.md",
        title="ADR-7",
        body=body,
        location=Location(kind="gitlab", ref={"path": "docs/adr-007.md"}, url="https://gitlab/adr-007"),
        type=EntryType.SPEC,
        version=version,
    )


@pytest.fixture()
def wired():
    store, summarizer = FakeEntryStore(), FakeSummarizer()
    return store, summarizer, ReconcileDocument(store, summarizer, FakeClock(NOW))


def test_an_opted_in_document_keeps_its_text_at_the_entrys_version(wired):
    # Covers: FR-CNT-02
    store, _, reconcile = wired
    result = reconcile.execute(adr(), content=KEEP)

    assert result.content_stored is True
    text, version = store.contents[result.entry_id]
    assert text.text == "# ADR-7\n\nWe chose Postgres over Mongo."
    assert version == store.get(result.entry_id).source_version == "sha-1"


def test_a_changed_document_replaces_its_text(wired):
    # Covers: FR-CNT-02
    store, _, reconcile = wired
    reconcile.execute(adr(), content=KEEP)
    result = reconcile.execute(adr(body="# ADR-7\n\nSuperseded by ADR-9.", version="sha-2"), content=KEEP)

    text, version = store.contents[result.entry_id]
    assert text.text.endswith("Superseded by ADR-9.")
    assert version == "sha-2"


def test_without_the_flag_no_text_is_kept(wired):
    # Covers: FR-CNT-03
    store, _, reconcile = wired
    reconcile.execute(adr())
    reconcile.execute(adr(version="sha-2", body="changed"), content=ContentPolicy.off())
    assert store.contents == {}


def test_turning_it_on_for_a_catalogued_document_costs_no_model_call(wired):
    # Covers: FR-CNT-04
    store, summarizer, reconcile = wired
    reconcile.execute(adr())  # catalogued before the source opted in
    assert summarizer.calls == ["platform/specs:docs/adr-007.md"]

    result = reconcile.execute(adr(), content=KEEP)
    assert result.action is Action.UNCHANGED
    assert result.summarized is False
    assert result.content_stored is True
    assert len(summarizer.calls) == 1, "storing text must never re-summarize"

    again = reconcile.execute(adr(), content=KEEP)
    assert again.content_stored is False, "identical text is not written twice"


def test_a_document_that_lost_its_body_loses_its_stored_text(wired):
    store, _, reconcile = wired
    result = reconcile.execute(adr(), content=KEEP)
    reconcile.execute(adr(body="   ", version="sha-2"), content=KEEP)
    assert result.entry_id not in store.contents


def test_an_over_cap_document_is_kept_cut_and_marked(wired):
    # Covers: FR-CNT-07
    store, _, reconcile = wired
    result = reconcile.execute(adr(body="z" * 5000), content=ContentPolicy(store=True, max_bytes=1024))
    text, _ = store.contents[result.entry_id]
    assert len(text.text) == 1024 and text.truncated and text.original_bytes == 5000
    assert store.get(result.entry_id).summary, "the summary is made from the whole document as before"
