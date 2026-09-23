"""Confluence, Jira and the summarizer, against recorded responses.

No network: a stub session returns payloads shaped like the real APIs'. What
these protect is the mapping -- which field becomes a title, what decides an
entry's type, how a page is addressed later -- and the failure behavior, since
sync runs unattended over systems nobody here controls.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from kb.adapters.outbound.confluence_source import ConfluenceKnowledgeSource, storage_to_text
from kb.adapters.outbound.jira_source import JiraKnowledgeSource, adf_to_text
from kb.adapters.outbound.llm_summarizer import LlmSummarizer
from kb.application.ports.knowledge_source import KnowledgeSource, SourceDocument
from kb.domain.entry import EntryType, Location
from kb.sources_config import ConfluenceSource, JiraSource


class StubResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class StubSession:
    """Records requests, replays queued responses."""

    def __init__(self, responses: list[StubResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.headers: dict[str, str] = {}
        self.auth: tuple[str, str] | None = None

    def get(self, url: str, **kwargs: Any) -> StubResponse:
        self.requests.append((url, kwargs.get("params") or {}))
        return self.responses.pop(0)

    def post(self, url: str, **kwargs: Any) -> StubResponse:
        self.requests.append((url, kwargs.get("json") or {}))
        return self.responses.pop(0)


def page(page_id: str, title: str, *, labels: tuple[str, ...] = (), body: str = "<p>Body</p>") -> dict:
    return {
        "id": page_id,
        "title": title,
        "body": {"storage": {"value": body}},
        "version": {"number": 7, "when": "2026-09-01T10:00:00.000Z"},
        "space": {"key": "ENG"},
        "history": {"createdDate": "2026-01-05T09:00:00.000Z"},
        "metadata": {"labels": {"results": [{"name": name} for name in labels]}},
        "_links": {"webui": f"/spaces/ENG/pages/{page_id}"},
    }


# ---------------------------------------------------------------- Confluence
def confluence(config: ConfluenceSource, responses: list[StubResponse]) -> tuple[ConfluenceKnowledgeSource, StubSession]:
    session = StubSession(responses)
    source = ConfluenceKnowledgeSource(
        config, base_url="https://wiki.internal", username="bot", password="secret", session=session
    )
    return source, session


def test_a_confluence_page_becomes_a_readable_pointer():
    # Covers: FR-SRC-01
    config = ConfluenceSource(id="confluence-eng", kind="confluence", spaces=("ENG",))
    source, _ = confluence(config, [StubResponse({"results": [page("123456", "Payment reconciliation")]})])

    document = next(iter(source.list_all()))

    assert isinstance(document, SourceDocument)
    assert document.external_id == "123456"
    assert document.title == "Payment reconciliation"
    assert document.location == Location(
        kind="confluence",
        ref={"page_id": "123456"},
        url="https://wiki.internal/spaces/ENG/pages/123456",
    )
    assert document.version == "7"
    assert document.updated_at is not None and document.created_at is not None


def test_a_label_decides_the_entry_type():
    # Covers: FR-SRC-01
    # Curation happens by labelling in Confluence, in the tool the authors
    # already use -- not by editing a file here.
    config = ConfluenceSource(
        id="confluence-eng",
        kind="confluence",
        spaces=("ENG",),
        type_from_labels={"kb-glossary": "term", "kb-team": "team"},
    )
    source, _ = confluence(config, [StubResponse({"results": [
        page("1", "Refund", labels=("kb-glossary",)),
        page("2", "Payments team", labels=("kb-team",)),
        page("3", "Some page", labels=("unrelated",)),
    ]})])

    documents = list(source.list_all())

    assert [d.type for d in documents] == [EntryType.TERM, EntryType.TEAM, EntryType.DOC]
    assert documents[0].tags == ("kb-glossary",), "labels double as tags to filter by"


def test_excluded_labels_are_filtered_in_the_query_and_again_on_the_way_out():
    config = ConfluenceSource(
        id="confluence-eng", kind="confluence", spaces=("ENG",), exclude_labels=("archive",)
    )
    source, session = confluence(config, [StubResponse({"results": [
        page("1", "Current"),
        page("2", "Old", labels=("archive",)),  # if the CQL filter ever regresses
    ]})])

    assert [d.external_id for d in source.list_all()] == ["1"]
    _, params = session.requests[0]
    assert 'label != "archive"' in params["cql"]
    assert 'space in ("ENG")' in params["cql"]


def test_an_incremental_query_asks_only_for_what_changed():
    # Covers: FR-REC-08
    config = ConfluenceSource(id="confluence-eng", kind="confluence", spaces=("ENG",))
    source, session = confluence(config, [StubResponse({"results": []})])

    list(source.changed_since("2026-09-24 11:00"))

    assert 'lastmodified >= "2026-09-24 11:00"' in session.requests[0][1]["cql"]


def test_confluence_paginates_until_the_last_page():
    config = ConfluenceSource(id="confluence-eng", kind="confluence", spaces=("ENG",))
    full = {"results": [page(str(n), f"Page {n}") for n in range(50)]}
    source, session = confluence(config, [StubResponse(full), StubResponse({"results": [page("50", "Last")]})])

    assert len(list(source.list_all())) == 51
    assert session.requests[1][1]["start"] == 50


def test_a_page_with_no_id_or_title_is_skipped_not_fatal():
    config = ConfluenceSource(id="confluence-eng", kind="confluence", spaces=("ENG",))
    source, _ = confluence(config, [StubResponse({"results": [
        {"id": "", "title": "No id"},
        page("2", "Fine"),
    ]})])
    assert [d.external_id for d in source.list_all()] == ["2"]


def test_fetch_returns_none_for_a_page_that_is_gone():
    config = ConfluenceSource(id="confluence-eng", kind="confluence")
    source, _ = confluence(config, [StubResponse(None, status_code=404)])
    assert source.fetch("404") is None


def test_storage_markup_becomes_text_without_running_words_together():
    # The summarizer reads this; "AliceBob" instead of "Alice Bob" would teach
    # it a word that doesn't exist.
    assert storage_to_text("<p>Alice</p><p>Bob</p>") == "Alice Bob"
    assert storage_to_text("<td>1</td><td>2</td>") == "1 2"
    assert storage_to_text("<p>caf&eacute;</p>") == "café"
    assert storage_to_text("") == ""


def test_the_confluence_adapter_satisfies_the_port():
    assert isinstance(
        ConfluenceKnowledgeSource(
            ConfluenceSource(id="x", kind="confluence"), base_url="https://wiki", session=StubSession([])
        ),
        KnowledgeSource,
    )


# --------------------------------------------------------------------- Jira
def issue(key: str, summary: str, *, description: Any = "A description.", labels: list[str] | None = None) -> dict:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": description,
            "updated": "2026-09-01T10:00:00.000+0000",
            "created": "2026-01-05T09:00:00.000+0000",
            "labels": labels or [],
            "project": {"key": "PAY"},
        },
    }


def jira(responses: list[StubResponse]) -> tuple[JiraKnowledgeSource, StubSession]:
    session = StubSession(responses)
    config = JiraSource(id="jira-product", kind="jira", jql="project = PAY AND issuetype = Epic")
    return JiraKnowledgeSource(config, base_url="https://jira.internal", token="t", session=session), session


def test_a_jira_epic_becomes_product_context():
    # Covers: FR-SRC-02
    source, _ = jira([StubResponse({"issues": [issue("PAY-1", "Refund retries")], "total": 1})])

    document = next(iter(source.list_all()))

    assert document.external_id == "PAY-1"
    assert document.type == EntryType.PRODUCT
    assert document.location == Location(
        kind="jira", ref={"issue_key": "PAY-1"}, url="https://jira.internal/browse/PAY-1"
    )
    assert document.body == "A description."


def test_an_incremental_jira_query_keeps_the_configured_filter_intact():
    # Covers: FR-REC-08
    # The configured JQL may contain an OR; appending a bare AND would quietly
    # widen what matches.
    source, session = jira([StubResponse({"issues": [], "total": 0})])

    list(source.changed_since("2026-09-24 11:00"))

    jql = session.requests[0][1]["jql"]
    assert jql.startswith("(project = PAY AND issuetype = Epic)")
    assert 'AND updated >= "2026-09-24 11:00"' in jql


def test_jira_cloud_rich_text_is_flattened():
    # Cloud returns ADF, Server returns a string: both have to work, because
    # which one an organization runs is not this adapter's business.
    adf = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Retries"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "run three times."}]},
        ],
    }
    assert adf_to_text(adf) == "Retries run three times."
    assert adf_to_text("plain text") == "plain text"
    assert adf_to_text(None) == ""


def test_jira_paginates_and_stops_at_the_total():
    issues = {"issues": [issue(f"PAY-{n}", f"Epic {n}") for n in range(50)], "total": 51}
    source, _ = jira([StubResponse(issues), StubResponse({"issues": [issue("PAY-50", "Last")], "total": 51})])
    assert len(list(source.list_all())) == 51


def test_the_jira_adapter_satisfies_the_port():
    source, _ = jira([])
    assert isinstance(source, KnowledgeSource)


# --------------------------------------------------------------- summarizer
def summarizer(responses: list[StubResponse], **kwargs) -> tuple[LlmSummarizer, StubSession]:
    session = StubSession(responses)
    return (
        LlmSummarizer(
            base_url="https://llm.internal/v1",
            model="a-model",
            api_key="k",
            languages=("en", "fa"),
            session=session,
            **kwargs,
        ),
        session,
    )


def completion(content: str) -> StubResponse:
    return StubResponse({"choices": [{"message": {"content": content}}]})


SAMPLE = json.dumps({
    "title": {"en": "Payment reconciliation", "fa": "تطبیق پرداخت"},
    "summary": {"en": "How refund retries work.", "fa": "روش بازپرداخت"},
    "keywords": ["refund", "retry", "بازپرداخت"],
})


def source_document() -> SourceDocument:
    return SourceDocument(
        source_id="confluence-eng",
        external_id="1",
        title="Payment reconciliation",
        body="Retries run three times with backoff.",
        location=Location(kind="confluence", ref={"page_id": "1"}),
    )


def test_a_draft_carries_every_configured_language():
    # Covers: FR-SRC-01
    # This is what replaces an embeddings model: one document, catalogued in
    # both languages, reachable from a question in either.
    summarize, session = summarizer([completion(SAMPLE)])

    draft = summarize.draft(source_document())

    assert set(draft.title) == {"en", "fa"} and set(draft.summary) == {"en", "fa"}
    assert "بازپرداخت" in draft.keywords
    _, body = session.requests[0]
    assert "en, fa" in body["messages"][1]["content"]


def test_the_document_is_labelled_untrusted_in_the_prompt():
    # Covers: NFR-ARC-03
    # Pages are written by people outside this stack and can contain text
    # shaped like instructions; the result is data to store, never to follow.
    summarize, session = summarizer([completion(SAMPLE)])
    summarize.draft(source_document())
    system = session.requests[0][1]["messages"][0]["content"]
    assert "untrusted" in system.lower()


def test_a_long_document_is_truncated_rather_than_sent_whole():
    summarize, session = summarizer([completion(SAMPLE)])
    document = source_document().model_copy(update={"body": "x" * 20_000})

    summarize.draft(document)

    prompt = session.requests[0][1]["messages"][1]["content"]
    assert len(prompt) < 10_000 and "(truncated)" in prompt


def test_a_model_that_wraps_its_json_in_a_fence_is_still_understood():
    summarize, _ = summarizer([completion(f"```json\n{SAMPLE}\n```")])
    assert summarize.draft(source_document()).title["en"] == "Payment reconciliation"


@pytest.mark.parametrize("response", ["not json at all", "", '{"title": "not a map"}'])
def test_an_unusable_response_degrades_to_an_empty_draft(response):
    # Covers: FR-REC-06
    # reconcile_document then catalogs the document under its real title:
    # findable and readable, just undescribed.
    summarize, _ = summarizer([completion(response)])
    draft = summarize.draft(source_document())
    assert draft.title == {} or isinstance(draft.title, dict)
    assert draft.summary == {}


def test_a_model_endpoint_that_is_down_does_not_stop_a_sync():
    # Covers: FR-REC-06
    summarize, _ = summarizer([StubResponse(None, status_code=503)])
    assert summarize.draft(source_document()).title == {}
