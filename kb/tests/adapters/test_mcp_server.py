"""The tool surface an agent sees.

What matters here is not that fastmcp works, but that the surface is the one the
spec promises: two read-only tools, arguments a model can fill in without
reading the spec, and a structured error instead of an exception when the
catalog is down.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from kb.adapters.inbound.mcp_server import INSTRUCTIONS, build_app
from kb.application.use_cases.get_entry import GetEntry
from kb.application.use_cases.search_catalog import SearchCatalog
from kb.domain.entry import Entry, EntryType, Location
from tests.application.fakes import FakeClock, FakeEntryStore

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

REFUNDS = Entry(
    id="confluence-eng:1",
    type=EntryType.DOC,
    title={"en": "Payment reconciliation", "fa": "تطبیق پرداخت"},
    summary={"en": "How refund retries are handled."},
    keywords=("refund", "retry", "بازپرداخت"),
    tags=("payments",),
    location=Location(kind="confluence", ref={"page_id": "1"}, url="https://wiki/1"),
    source_id="confluence-eng",
    last_seen_at=NOW - timedelta(hours=1),
)


class StubContainer:
    def __init__(self, store: FakeEntryStore) -> None:
        clock = FakeClock(NOW)
        self.store = store
        self.search = SearchCatalog(store, clock)
        self.get_entry = GetEntry(store, clock)


@pytest.fixture()
def app():
    store = FakeEntryStore()
    store.upsert(REFUNDS)
    return build_app(StubContainer(store))


@pytest.fixture()
def tools(app):
    return {tool.name: tool for tool in asyncio.run(app.list_tools())}


def call(app, name, **arguments):
    """Invoke a tool the way a client would, and hand back its payload."""
    tool = asyncio.run(app.get_tool(name))
    return asyncio.run(tool.run(arguments)).structured_content


def test_exactly_two_read_only_tools_are_exposed(tools):
    # Covers: FR-MCP-01
    # Anything that writes belongs to the operator CLI, not to a model.
    assert set(tools) == {"kb_search", "kb_get"}


def test_the_tool_schemas_tell_a_model_how_to_call_them(tools):
    # Covers: FR-MCP-02
    search = tools["kb_search"]
    parameters = search.parameters["properties"]
    assert set(parameters) == {"queries", "types", "tags", "limit"}
    assert parameters["queries"]["type"] == "array", "several queries in one call"
    assert search.parameters["required"] == ["queries"]
    # The instructions that make this work have to reach the model, not just
    # the spec: search in both languages, and read the real document rather
    # than answering from the summary.
    assert "language" in parameters["queries"]["description"].lower()
    assert "language" in search.description.lower()
    assert "fetch" in search.description and "pointer, not a source" in search.description
    assert "language" in INSTRUCTIONS.lower()


def test_search_returns_hits_with_the_next_call_to_make(app):
    # Covers: FR-MCP-02, FR-ENT-09
    payload = call(app, "kb_search", queries=["refund retries", "بازپرداخت"])
    assert [hit["id"] for hit in payload["hits"]] == [REFUNDS.id]
    hit = payload["hits"][0]
    assert hit["fetch"] == {"tool": "confluence_get_page", "args": {"page_id": "1"}}
    assert hit["stale"] is False
    assert "body" not in hit and "content" not in hit, "the catalog never hands back document text"


def test_search_reports_an_empty_catalog_rather_than_failing(app):
    payload = call(app, "kb_search", queries=["quarterly hiring plan"])
    assert payload["hits"] == []
    assert payload["notes"] == ["nothing in the catalog matched"]


def test_get_returns_one_entry_and_a_structured_not_found(app):
    # Covers: FR-MCP-01
    assert call(app, "kb_get", id=REFUNDS.id)["entry"]["id"] == REFUNDS.id
    assert call(app, "kb_get", id="nope")["error"]["type"] == "not_found"


def test_an_unknown_type_filter_is_reported_not_raised(app):
    # Covers: FR-MCP-03
    payload = call(app, "kb_search", queries=["refund"], types=["diagram"])
    assert payload["error"]["type"] == "bad_argument"


def test_a_catalog_that_is_down_returns_an_error_the_agent_can_explain(app, monkeypatch):
    # Covers: FR-MCP-03
    # An exception here would fail the whole turn over a search that was only
    # meant to add context.
    def explode(*args, **kwargs):
        raise OSError("server closed the connection unexpectedly")

    monkeypatch.setattr(SearchCatalog, "execute", explode)
    monkeypatch.setattr(GetEntry, "execute", explode)

    for payload in (call(app, "kb_search", queries=["refund"]), call(app, "kb_get", id=REFUNDS.id)):
        assert payload["error"]["type"] == "catalog_unavailable"
        assert "server closed" in payload["error"]["message"]


def test_the_server_asks_for_no_credentials(app):
    # Covers: FR-MCP-04
    # The catalog is shared: there is nothing per-user to supply, so there is
    # no auth provider and no credential-shaped tool argument to spoof.
    assert app.auth is None
    for tool in asyncio.run(app.list_tools()):
        names = set(tool.parameters["properties"])
        assert not {"token", "password", "credential", "api_key", "user"} & names
