"""The CLI's contract, which scripts and humans both depend on: one JSON
document on stdout, exit 0 for anything handled.

Wired against the in-memory fakes, so these run without a database -- the
Postgres-backed behavior is tested in tests/integration/.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from kb.adapters.inbound import cli
from kb.application.use_cases.get_entry import GetEntry
from kb.application.use_cases.search_catalog import SearchCatalog
from kb.config import Settings
from kb.domain.entry import Entry, EntryType, Location
from kb.domain.merge import Override
from tests.application.fakes import FakeClock, FakeEntryStore

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

REFUNDS = Entry(
    id="confluence-eng:1",
    type=EntryType.DOC,
    title={"en": "Payment reconciliation", "fa": "تطبیق پرداخت"},
    summary={"en": "How refund retries are handled."},
    keywords=("refund", "retry"),
    tags=("payments",),
    owner_team="payments",
    location=Location(kind="confluence", ref={"page_id": "1"}, url="https://wiki/1"),
    source_id="confluence-eng",
    last_seen_at=NOW - timedelta(hours=1),
)


class FakeContainer:
    """What `container.build` would return, minus the database."""

    def __init__(self, store: FakeEntryStore) -> None:
        clock = FakeClock(NOW)
        self.settings = Settings(database_url="postgresql://fake/kb")
        self.store = store
        self.search = SearchCatalog(store, clock)
        self.get_entry = GetEntry(store, clock)
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def wired(monkeypatch):
    store = FakeEntryStore()
    store.upsert(REFUNDS)
    container = FakeContainer(store)

    # Extra fake-store behavior the CLI reaches for, which the use cases don't.
    store.top_misses = lambda *, limit=20: [{"query": "hiring plan", "times": 3, "last_seen": NOW}]
    store.status = lambda: {"entries": {"live": 1}}
    store.migrate = lambda: []

    monkeypatch.setenv("KB_DATABASE_URL", "postgresql://fake/kb")
    monkeypatch.setattr("kb.container.build", lambda settings=None: container)
    return container


def run(capsys, *argv) -> dict:
    # Covers: FR-CLI-01
    exit_code = cli.main(list(argv))
    stdout = capsys.readouterr().out
    assert exit_code == 0, "a handled outcome must exit 0"
    assert stdout.count("\n") == 1, "exactly one JSON document, one line"
    return json.loads(stdout)


def test_search_prints_hits_with_the_next_call_to_make(wired, capsys):
    # Covers: FR-CLI-02, FR-ENT-09
    payload = run(capsys, "search", "--query", "refund retries", "--query", "بازپرداخت")
    assert [hit["id"] for hit in payload["hits"]] == [REFUNDS.id]
    assert payload["hits"][0]["fetch"] == {"tool": "confluence_get_page", "args": {"page_id": "1"}}
    assert payload["queries"] == ["refund retries", "بازپرداخت"]


def test_search_filters_and_limit_reach_the_use_case(wired, capsys):
    # Covers: FR-CLI-02
    assert run(capsys, "search", "--query", "refund", "--type", "term")["hits"] == []
    assert run(capsys, "search", "--query", "refund", "--tag", "payments")["hits"] != []
    assert len(run(capsys, "search", "--query", "refund", "--limit", "1")["hits"]) == 1


def test_get_returns_one_entry_and_reports_a_missing_id(wired, capsys):
    # Covers: FR-CLI-02, FR-CLI-03
    payload = run(capsys, "get", "--id", REFUNDS.id)
    assert payload["entry"]["id"] == REFUNDS.id
    assert payload["stale"] is False

    missing = run(capsys, "get", "--id", "nope")
    assert missing["error"]["type"] == "not_found"


def test_override_set_and_clear(wired, capsys):
    # Covers: FR-CLI-02
    payload = run(capsys, "override", "set", "--id", REFUNDS.id,
                  "--summary", "en=Mine", "--exclude", "--note", "duplicate")
    assert payload["override"]["summary"] == {"en": "Mine"}
    assert wired.store.get_override(REFUNDS.id).exclude is True
    assert run(capsys, "search", "--query", "refund")["hits"] == []

    wired.store.clear_override = lambda entry_id: wired.store.overrides.pop(entry_id, None)
    assert run(capsys, "override", "clear", "--id", REFUNDS.id) == {"cleared": REFUNDS.id}


def test_a_malformed_localized_argument_is_reported_not_raised(wired, capsys):
    # Covers: FR-CLI-03
    payload = run(capsys, "override", "set", "--id", REFUNDS.id, "--summary", "no-language-prefix")
    assert payload["error"]["type"] == "bad_argument"
    assert "LANG=TEXT" in payload["error"]["message"]


def test_gaps_and_status_answer_is_this_thing_working(wired, capsys):
    # Covers: FR-CLI-04
    assert run(capsys, "gaps")["gaps"][0]["query"] == "hiring plan"
    assert run(capsys, "status")["entries"]["live"] == 1


def test_an_unset_setting_is_reported_with_the_variable_named(monkeypatch, capsys):
    # Covers: FR-CLI-03
    monkeypatch.delenv("KB_DATABASE_URL", raising=False)
    payload = run(capsys, "status")
    assert payload["error"] == {
        "type": "missing_setting",
        "message": "KB_DATABASE_URL is not set",
        "variable": "KB_DATABASE_URL",
    }


def test_an_unreachable_database_is_reported_not_traced(monkeypatch, capsys):
    # Covers: FR-CLI-03
    monkeypatch.setenv("KB_DATABASE_URL", "postgresql://nowhere/kb")

    def explode(settings=None):
        raise OSError("connection refused")

    monkeypatch.setattr("kb.container.build", explode)
    payload = run(capsys, "status")
    assert payload["error"]["type"] == "database_unavailable"
    assert "connection refused" in payload["error"]["message"]


def test_the_connection_is_closed_even_when_a_command_reports_an_error(wired, capsys):
    run(capsys, "get", "--id", "nope")
    assert wired.closed is True


def test_persian_output_is_not_escaped(wired, capsys):
    # A catalog people read in a terminal has to be readable there.
    cli.main(["search", "--query", "refund"])
    assert "تطبیق" in capsys.readouterr().out


def test_the_clock_runs_in_the_configured_timezone():
    # Covers: FR-SCH-06
    from zoneinfo import ZoneInfo

    from kb.application.ports.clock import SystemClock
    from kb.config import Settings

    settings = Settings(database_url="postgresql://x/y", timezone="Asia/Tehran")
    assert settings.tzinfo == ZoneInfo("Asia/Tehran")
    assert SystemClock(settings.tzinfo).now().tzinfo == ZoneInfo("Asia/Tehran")
    # Default stays UTC: a deployment that says nothing gets something
    # unambiguous rather than the container's accidental local time.
    assert Settings(database_url="postgresql://x/y").tzinfo is timezone.utc


def test_an_unknown_timezone_fails_at_startup_with_the_name_that_was_wrong():
    # Covers: FR-SCH-06
    from pydantic import ValidationError as _VE

    from kb.config import Settings

    with pytest.raises(_VE) as excinfo:
        Settings(database_url="postgresql://x/y", timezone="Mars/Olympus")
    assert "Mars/Olympus" in str(excinfo.value) and "IANA" in str(excinfo.value)


def test_check_reports_each_dependency_and_which_one_is_not_ready(wired, capsys, monkeypatch):
    # Covers: FR-CLI-05
    # Each of these fails quietly otherwise: no summarizer means undescribed
    # entries, an unreadable source config means a scheduler that logs and
    # sleeps.
    import pathlib

    sources = pathlib.Path(wired.settings.sources_file)

    payload = run(capsys, "check")

    assert payload["ready"] is False
    assert payload["checks"]["database"]["ok"] is True
    summarizer = payload["checks"]["summarizer"]
    assert summarizer["ok"] is False
    assert "KB_SUMMARIZER_URL" in summarizer["error"]
    assert "undescribed" in summarizer["consequence"]
    assert payload["checks"]["sources"]["ok"] is False
    del sources


def test_check_reports_a_database_that_is_down_rather_than_raising(wired, capsys):
    # Covers: FR-CLI-05
    def explode():
        raise OSError("connection refused")

    wired.store.status = explode
    payload = run(capsys, "check")
    assert payload["checks"]["database"] == {"ok": False, "error": "connection refused"}


def test_check_probes_the_summarizer_when_one_is_configured(wired, capsys, monkeypatch, tmp_path):
    # Covers: FR-CLI-05, FR-CFG-08
    from kb.config import Settings

    wired.settings = Settings(
        database_url="postgresql://fake/kb",
        summarizer_url="https://llm.internal/v1",
        summarizer_model="a-model",
        summarizer_api_key="k",
        sources_file=str(tmp_path / "absent.yaml"),
    )
    monkeypatch.setattr(
        "kb.container.build_summarizer",
        lambda settings: type("S", (), {"check": lambda self: {"ok": True, "models": ["a-model"], "model_served": True}})(),
    )

    payload = run(capsys, "check")

    assert payload["checks"]["summarizer"]["ok"] is True
    assert payload["checks"]["summarizer"]["authenticated"] is True
    assert payload["checks"]["sources"]["ok"] is False, "the file does not exist"
