"""The admin console's HTTP door, with in-memory fakes behind it.

Authentication and the CSRF rule are the console's real front line, so most of
what is here is about who gets in and what a hostile page cannot make a logged-in
browser do. The rest proves the read models say what the run history says.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from kb.adapters.inbound.admin import AdminServices, build_app
from kb.adapters.inbound.admin.auth import COOKIE, Auth
from kb.adapters.outbound.source_factory import BUILDABLE_KINDS, missing_credentials
from kb.adapters.outbound.sources_editor import RuamelSourceEditor
from kb.adapters.outbound.sources_file import FileSourcesFile
from kb.application.ports.heartbeat import Heartbeat
from kb.application.use_cases.draft_summary import DraftSummary
from kb.application.use_cases.edit_sources import EditSources
from kb.application.use_cases.get_entry import GetEntry
from kb.application.use_cases.manage_forms import ManageForms
from kb.application.use_cases.request_sync import RequestSync
from kb.application.ports.sync_queue import RequestStatus, SyncRequest
from kb.config import AdminSettings, MissingSetting
from kb.domain.runs import RunStatus, Trigger
from tests.application.fakes import (
    FakeClock,
    FakeEntryStore,
    FakeFormRecords,
    FakeHeartbeats,
    FakeRunLog,
    FakeSourceHistory,
    FakeSummarizer,
    FakeSyncQueue,
)

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
TOKEN = "t" * 40
CSRF = {"X-KB-Admin": "1"}

CONFIG = """
approved: true
defaults:
  mechanisms:
    incremental:  { enabled: true, every: 15m }
    full_scrape:  { enabled: true, at: "03:00" }
    lazy_refresh: { enabled: true }
sources:
  - id: confluence-eng
    kind: confluence
    enabled: true
    spaces: [ENG]
    store_raw_content: true
  - id: jira-product
    kind: jira
    enabled: false
    jql: "project = PAY"
"""


class ConsoleStore(FakeEntryStore):
    """What the console reads from the catalog."""

    def __init__(self) -> None:
        super().__init__()
        self.gap_rows = [{"query": "hiring plan", "times": 3, "last_seen": NOW}]

    def status(self):
        return {
            "entries": {"live": 7, "soft_deleted": 1, "searchable": 6},
            "by_source": [
                {"source_id": "confluence-eng", "entries": 7},
                {"source_id": "retired-wiki", "entries": 3},
                {"source_id": "form", "entries": 2},
            ],
            "by_type": [{"type": "doc", "entries": 7}],
            "overrides": 2,
            "gaps": 3,
            "raw_content": {"documents": 4, "truncated": 0, "text_bytes": 2048, "disk_bytes": 8192},
            "migrations": ["001_initial.sql"],
            "checkpoints": [],
        }

    def top_misses(self, *, limit=20):
        return self.gap_rows[:limit]

    def content_by_source(self):
        return {"confluence-eng": {"documents": 4, "text_bytes": 2048}}

    def refresh_queue_summary(self):
        return {"pending": 1, "gave_up": 0, "failures": []}


class Summarizer:
    def __init__(self, ok=True):
        self.ok = ok

    def check(self):
        return {"ok": self.ok, "model": "m"} if self.ok else {"ok": False, "error": "no system role"}


@pytest.fixture()
def world(tmp_path):
    sources = tmp_path / "kb-sources.yaml"
    sources.write_text(CONFIG, encoding="utf-8")
    services = AdminServices(
        store=ConsoleStore(),
        runs=FakeRunLog(),
        queue=FakeSyncQueue(),
        heartbeats=FakeHeartbeats(),
        clock=FakeClock(NOW),
        sources_file=sources,
        languages=("en", "fa"),
        summarizer=Summarizer(),
    )
    services.forms = FakeFormRecords()
    services.manage_forms = ManageForms(
        services.forms, RequestSync(services.queue, services.clock), services.clock
    )
    services.draft_summary = DraftSummary(FakeSummarizer())
    services.get_entry = GetEntry(services.store, services.clock, queue_refresh=False)
    services.sources_editor = EditSources(
        FileSourcesFile(sources),
        RuamelSourceEditor(),
        FakeSourceHistory(),
        services.clock,
        credentials=lambda: services.heartbeats.last_beat().credentials,
        buildable_kinds=BUILDABLE_KINDS,
        missing_credentials=missing_credentials,
    )
    services.heartbeats.beat(
        Heartbeat(beat_at=NOW - timedelta(seconds=5),
                  credentials={"confluence-eng": {"KB_CONFLUENCE_BASE_URL": True, "KB_CONFLUENCE_PAT": True}})
    )
    return services


def make_client(services, token=TOKEN, **settings):
    app = build_app(settings=AdminSettings(token=token, **settings), services=services)
    return TestClient(app)


@pytest.fixture()
def anon(world):
    return make_client(world)


@pytest.fixture()
def client(world):
    c = make_client(world)
    assert c.post("/api/session", json={"token": TOKEN}, headers=CSRF).status_code == 200
    return c


def finished(world, *, source="confluence-eng", error=None, rejected=(), triggered_by=Trigger.SCHEDULED,
             mechanism="incremental", at=NOW, **counts):
    run_id = world.runs.start_run(source_id=source, mechanism=mechanism, started_at=at, triggered_by=triggered_by)
    world.runs.finish_run(run_id, finished_at=at + timedelta(seconds=2), counts=counts,
                          rejected=rejected, error=error)
    return run_id


# -- who gets in (FR-ADM-01, FR-ADM-02) ----------------------------------------


def test_the_console_refuses_to_start_without_a_long_enough_token():
    # Covers: FR-ADM-01
    with pytest.raises(MissingSetting):
        AdminSettings.from_env({})
    with pytest.raises(ValueError, match="at least 32"):
        AdminSettings.from_env({"KB_ADMIN_TOKEN": "short"})
    assert AdminSettings.from_env({"KB_ADMIN_TOKEN": TOKEN}).host == "127.0.0.1", "loopback by default"


def test_tokens_are_compared_in_constant_time():
    # Covers: FR-ADM-01
    assert "compare_digest" in inspect.getsource(Auth.token_matches)


def test_every_endpoint_but_the_health_check_needs_a_login(anon):
    # Covers: FR-ADM-01
    assert anon.get("/health").status_code == 200
    for path in ("/api/overview", "/api/sources", "/api/schedules", "/api/runs", "/api/runs/1",
                 "/api/issues", "/api/gaps", "/api/config", "/api/sync/requests", "/api/forms",
                 "/api/forms/x", "/api/forms/x/content"):
        assert anon.get(path).status_code == 401, path
    assert anon.post("/api/sync", json={}, headers=CSRF).status_code == 401
    assert anon.delete("/api/sync/requests/1", headers=CSRF).status_code == 401
    assert anon.post("/api/check", headers=CSRF).status_code == 401
    assert anon.post("/api/forms", json={}, headers=CSRF).status_code == 401
    assert anon.put("/api/forms/x", json={}, headers=CSRF).status_code == 401
    assert anon.delete("/api/forms/x", headers=CSRF).status_code == 401
    assert anon.post("/api/forms/draft-summary", json={}, headers=CSRF).status_code == 401
    assert anon.get("/api/session").json() == {"authenticated": False}


def test_a_wrong_token_is_refused_and_the_right_one_logs_in(anon):
    # Covers: FR-ADM-01
    assert anon.post("/api/session", json={"token": "x" * 40}, headers=CSRF).status_code == 401
    assert anon.get("/api/overview").status_code == 401
    assert anon.post("/api/session", json={"token": TOKEN}, headers=CSRF).status_code == 200
    assert anon.get("/api/session").json() == {"authenticated": True}
    assert anon.get("/api/overview").status_code == 200


def test_a_script_can_use_the_token_as_a_bearer(anon):
    # Covers: FR-ADM-01
    assert anon.get("/api/overview", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert anon.get("/api/overview", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_the_session_cookie_is_http_only_strict_and_signed(anon):
    # Covers: FR-ADM-02
    response = anon.post("/api/session", json={"token": TOKEN}, headers=CSRF)
    cookie = response.headers["set-cookie"].lower()
    assert COOKIE in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert TOKEN not in response.headers["set-cookie"], "the cookie is not the token"


def test_secure_cookies_can_be_required_behind_tls(world):
    # Covers: FR-ADM-02
    c = make_client(world, cookie_secure=True)
    cookie = c.post("/api/session", json={"token": TOKEN}, headers=CSRF).headers["set-cookie"].lower()
    assert "secure" in cookie


def test_a_cookie_from_another_token_or_a_forged_one_is_worthless(world):
    # Covers: FR-ADM-02
    first = make_client(world, token="a" * 40)
    first.post("/api/session", json={"token": "a" * 40}, headers=CSRF)
    stolen = first.cookies.get(COOKIE)

    second = make_client(world, token="b" * 40)
    second.cookies.set(COOKIE, stolen)
    assert second.get("/api/overview").status_code == 401, "changing the token ends every session"

    second.cookies.set(COOKIE, "AAAA.BBBB", domain="")
    assert second.get("/api/overview").status_code == 401


def test_a_session_ends_when_its_time_is_up(world):
    # Covers: FR-ADM-02
    clock = {"now": 1_000_000.0}
    app = build_app(settings=AdminSettings(token=TOKEN, session_hours=1), services=world,
                    now=lambda: clock["now"])
    c = TestClient(app)
    c.post("/api/session", json={"token": TOKEN}, headers=CSRF)
    assert c.get("/api/overview").status_code == 200
    clock["now"] += 3601
    assert c.get("/api/overview").status_code == 401


def test_repeated_wrong_logins_are_slowed_down(anon):
    # Covers: FR-ADM-02
    for _ in range(5):
        assert anon.post("/api/session", json={"token": "x" * 40}, headers=CSRF).status_code == 401
    locked = anon.post("/api/session", json={"token": TOKEN}, headers=CSRF)
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0


def test_logging_out_clears_the_cookie(client):
    # Covers: FR-ADM-02
    assert client.delete("/api/session", headers=CSRF).status_code == 200
    assert client.get("/api/overview").status_code == 401


# -- what a hostile page cannot do (FR-ADM-03) -----------------------------------


def test_a_logged_in_browser_cannot_be_made_to_change_things_by_another_site(client):
    # Covers: FR-ADM-03
    body = {"source_id": "confluence-eng", "mode": "full"}
    assert client.post("/api/sync", json=body).status_code == 403, "no console header"
    evil = client.post("/api/sync", json=body, headers={**CSRF, "Origin": "https://evil.example"})
    assert evil.status_code == 403
    good = client.post("/api/sync", json=body, headers={**CSRF, "Origin": "http://testserver"})
    assert good.status_code == 202


def test_every_state_changing_endpoint_needs_the_header(client, world):
    # Covers: FR-ADM-03
    request_id = world.queue.enqueue(SyncRequest(source_id="confluence-eng"), at=NOW)
    for method, path in (
        ("post", "/api/sync"),
        ("post", "/api/check"),
        ("post", "/api/sources/confluence-eng/purge-content"),
        ("post", "/api/sources/retired-wiki/retire"),
        ("post", "/api/sources/preview"),
        ("post", "/api/sources/apply"),
        ("post", "/api/forms"),
        ("put", "/api/forms/x"),
        ("delete", "/api/forms/x"),
        ("post", "/api/forms/draft-summary"),
        ("delete", f"/api/sync/requests/{request_id}"),
    ):
        assert getattr(client, method)(path).status_code == 403, path
    assert world.queue.requests[0].status is RequestStatus.QUEUED, "nothing was cancelled"


def test_login_itself_needs_the_console_header(anon):
    # Covers: FR-ADM-03
    assert anon.post("/api/session", json={"token": TOKEN}).status_code == 403
    assert anon.post("/api/session", json={"token": TOKEN},
                     headers={**CSRF, "Origin": "https://evil.example"}).status_code == 403


def test_a_bearer_token_does_not_need_the_header(anon):
    # Covers: FR-ADM-03
    # Nothing ambient to abuse: a hostile page cannot make a browser attach it.
    response = anon.post("/api/sync", json={"source_id": "confluence-eng"},
                         headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 202


def test_responses_are_not_cached_and_cannot_be_framed(client):
    # Covers: FR-ADM-03
    response = client.get("/api/overview")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


# -- the overview (FR-ADM-05) ------------------------------------------------------


def test_the_overview_reports_the_catalog_the_scheduler_and_the_last_day(client, world):
    # Covers: FR-ADM-05
    finished(world, error="down", at=NOW - timedelta(minutes=30))
    finished(world, created=3)
    world.queue.enqueue(SyncRequest(source_id="confluence-eng"), at=NOW)

    data = client.get("/api/overview").json()

    assert data["entries"] == {"live": 7, "soft_deleted": 1, "searchable": 6}
    assert data["scheduler"]["alive"] is True
    assert data["approved"] == {"value": True, "from": "file"}
    assert data["runs_24h"]["runs"] == 2 and data["runs_24h"]["failed"] == 1
    assert data["queue"] == {"queued": 1, "running": 0}
    assert data["sources"] == {"configured": 2, "enabled": 1}
    assert data["last_run"]["created"] == 3
    assert data["refresh_queue"]["pending"] == 1
    assert data["health"] == "degraded"
    assert any("failed" in problem for problem in data["problems"])


def test_a_silent_scheduler_is_an_error(client, world):
    # Covers: FR-ADM-05
    world.heartbeats.beats.clear()
    world.heartbeats.beat(Heartbeat(beat_at=NOW - timedelta(minutes=5)))
    data = client.get("/api/overview").json()
    assert data["health"] == "error" and data["scheduler"]["alive"] is False
    assert "has not reported" in data["problems"][0]

    world.heartbeats.beats.clear()
    data = client.get("/api/overview").json()
    assert data["health"] == "error" and "never reported" in data["problems"][0]


def test_a_run_in_progress_makes_the_catalog_syncing(client, world):
    # Covers: FR-ADM-05
    world.runs.start_run(source_id="confluence-eng", mechanism="full_scrape", started_at=NOW)
    assert client.get("/api/overview").json()["health"] == "syncing"


def test_syncing_switched_off_is_reported_with_where_it_came_from(client, world):
    # Covers: FR-ADM-05, FR-CFG-05
    world.sources_file.write_text(CONFIG.replace("approved: true", "approved: false"), encoding="utf-8")
    data = client.get("/api/overview").json()
    assert data["approved"] == {"value": False, "from": "file"}
    assert data["health"] == "degraded"
    assert "switched off" in " ".join(data["problems"])


def test_an_unreadable_source_file_is_reported_not_a_crash(client, world):
    # Covers: FR-ADM-05
    world.sources_file.write_text("sources: [\n", encoding="utf-8")
    data = client.get("/api/overview").json()
    assert data["config_error"] and data["sources"] == {"configured": 0, "enabled": 0}
    assert client.get("/api/sources").json()["config_error"]
    assert client.get("/api/schedules").json()["config_error"]


# -- sources, schedules and runs (FR-ADM-06, FR-ADM-07) ---------------------------------


def test_each_source_card_carries_its_policy_counts_and_last_run(client, world):
    # Covers: FR-ADM-05
    finished(world, created=2)
    cards = {c["id"]: c for c in client.get("/api/sources").json()["sources"]}

    eng = cards["confluence-eng"]
    assert eng["kind"] == "confluence" and eng["enabled"] is True
    assert eng["store_raw_content"] is True and eng["raw_content_max_bytes"] > 0
    assert eng["entries"] == 7 and eng["content"] == {"documents": 4, "text_bytes": 2048}
    assert eng["mechanisms"]["incremental"]["every"] == "15m"
    assert eng["settings"]["spaces"] == ["ENG"]
    assert eng["last_run"]["created"] == 2

    jira = cards["jira-product"]
    assert jira["enabled"] is False and jira["store_raw_content"] is False
    assert jira["entries"] == 0 and jira["last_run"] is None


def test_runs_are_listed_newest_first_filtered_and_paged(client, world):
    # Covers: FR-ADM-06
    ids = [finished(world, source="a" if i % 2 else "b", at=NOW + timedelta(minutes=i),
                    error="x" if i == 3 else None) for i in range(5)]

    listing = client.get("/api/runs").json()
    assert [r["id"] for r in listing["runs"]] == ids[::-1]
    assert [r["id"] for r in client.get("/api/runs?source=a").json()["runs"]] == [ids[3], ids[1]]
    assert [r["id"] for r in client.get("/api/runs?status=error").json()["runs"]] == [ids[3]]

    page = client.get(f"/api/runs?limit=2&before={ids[4]}").json()
    assert [r["id"] for r in page["runs"]] == [ids[3], ids[2]] and page["next_before"] == ids[2]
    assert client.get("/api/runs?status=bogus").status_code == 400


def test_one_run_is_readable_in_full(client, world):
    # Covers: FR-ADM-06
    run_id = finished(world, rejected=("confluence-eng:9: no page id",), created=1, seen=2,
                      triggered_by=Trigger.MANUAL)
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "warning" and detail["triggered_by"] == "manual"
    assert detail["rejected"] == ["confluence-eng:9: no page id"]
    assert detail["duration_ms"] == 2000 and detail["seen"] == 2
    assert client.get("/api/runs/999").status_code == 404
    assert client.get("/api/runs/abc").status_code == 404


def test_the_schedule_lists_every_mechanism_with_its_last_result(client, world):
    # Covers: FR-ADM-07
    finished(world, created=1, at=NOW - timedelta(minutes=5))

    data = client.get("/api/schedules").json()
    rows = {(r["source_id"], r["kind"]): r for r in data["rows"]}

    incremental = rows[("confluence-eng", "incremental")]
    assert incremental["enabled"] is True and incremental["cadence"] == "every 15m"
    assert incremental["last_result"]["status"] == "success"
    assert incremental["due_at"].startswith("2026-10-14T12:10"), "last run + 15 minutes"
    nightly = rows[("confluence-eng", "full_scrape")]
    assert nightly["cadence"] == "daily at 03:00" and nightly["due_at"].startswith("2026-10-15T03:00")

    off = rows[("jira-product", "incremental")]
    assert off["enabled"] is False and off["reason"] == "source disabled", "shown, not omitted"
    assert ("jira-product", "full_scrape") in rows
    assert (None, "lazy_refresh") in rows, "the refresh drain belongs to no source"


def test_a_running_job_shows_as_running_in_the_schedule(client, world):
    # Covers: FR-ADM-07
    world.runs.start_run(source_id="confluence-eng", mechanism="incremental", started_at=NOW)
    rows = {(r["source_id"], r["kind"]): r for r in client.get("/api/schedules").json()["rows"]}
    assert rows[("confluence-eng", "incremental")]["running"] is True


# -- asking the scheduler (FR-ADM-08, FR-ADM-09) ------------------------------------------


def test_a_sync_request_returns_at_once_and_waits_for_the_scheduler(client, world):
    # Covers: FR-ADM-08
    response = client.post("/api/sync", headers=CSRF, json={
        "source_id": "confluence-eng", "mode": "full", "dry_run": False, "force": True})
    assert response.status_code == 202
    [queued] = world.queue.requests
    assert (queued.source_id, queued.mode, queued.forced, queued.dry_run) == ("confluence-eng", "full", True, False)
    assert queued.status is RequestStatus.QUEUED
    assert world.runs.runs == [], "the console ran nothing itself"

    listing = client.get("/api/sync/requests").json()["requests"]
    assert listing[0]["id"] == response.json()["request_id"] and listing[0]["position"] == 1


def test_every_enabled_source_can_be_requested_at_once(client, world):
    # Covers: FR-ADM-08
    assert client.post("/api/sync", headers=CSRF, json={"source_id": "*"}).status_code == 202
    assert world.queue.requests[0].source_id == "*"


@pytest.mark.parametrize("body, message", [
    ({"source_id": "nope"}, "no source"),
    ({"source_id": "confluence-eng", "mode": "sideways"}, "mode"),
    ({"source_id": "confluence-eng", "dry_run": "yes"}, "true or false"),
    ({"source_id": "confluence-eng", "dry_run": True, "force": True}, "dry run cannot be forced"),
])
def test_an_invalid_request_is_refused_and_says_why(client, world, body, message):
    # Covers: FR-ADM-08
    response = client.post("/api/sync", headers=CSRF, json=body)
    assert response.status_code == 400 and message in response.json()["error"]["message"]
    assert world.queue.requests == []


def test_a_request_that_is_not_json_is_refused(client):
    # Covers: FR-ADM-08
    response = client.post("/api/sync", headers=CSRF, content="not json")
    assert response.status_code == 400


def test_pressing_the_button_twice_queues_it_once(client, world):
    # Covers: FR-ADM-08
    body = {"source_id": "confluence-eng", "mode": "incremental"}
    first = client.post("/api/sync", headers=CSRF, json=body).json()["request_id"]
    second = client.post("/api/sync", headers=CSRF, json=body).json()["request_id"]
    assert first == second and len(world.queue.requests) == 1
    client.post("/api/sync", headers=CSRF, json={**body, "dry_run": True})
    assert len(world.queue.requests) == 2, "a different request is a different request"


def test_a_waiting_request_can_be_cancelled_but_not_one_that_started(client, world):
    # Covers: FR-ADM-08
    waiting = client.post("/api/sync", headers=CSRF, json={"source_id": "confluence-eng"}).json()["request_id"]
    assert client.delete(f"/api/sync/requests/{waiting}", headers=CSRF).status_code == 200
    assert world.queue.requests[0].status is RequestStatus.CANCELLED

    started = world.queue.enqueue(SyncRequest(source_id="confluence-eng", mode="full"), at=NOW)
    world.queue.claim_next(at=NOW)
    assert client.delete(f"/api/sync/requests/{started}", headers=CSRF).status_code == 409
    assert client.delete("/api/sync/requests/abc", headers=CSRF).status_code == 404


def test_purging_stored_text_is_a_request_too(client, world):
    # Covers: FR-ADM-09
    response = client.post("/api/sources/confluence-eng/purge-content", headers=CSRF)
    assert response.status_code == 202
    assert world.queue.requests[0].kind.value == "purge_content"
    assert client.post("/api/sources/nope/purge-content", headers=CSRF).status_code == 400


# -- what needs attention (FR-ADM-10) -------------------------------------------------


def test_the_issues_view_gathers_failures_rejections_and_gaps(client, world):
    # Covers: FR-ADM-10
    finished(world, rejected=("confluence-eng:9: no page id",))
    finished(world, error="wiki is down", at=NOW + timedelta(minutes=1))
    finished(world, created=5, at=NOW + timedelta(minutes=2))  # a good run is not an issue

    data = client.get("/api/issues").json()

    assert [r["status"] for r in data["runs"]] == ["error", "warning"]
    assert data["runs"][1]["rejected"] == ["confluence-eng:9: no page id"]
    assert data["gaps"][0]["query"] == "hiring plan"
    assert data["refresh_queue"]["pending"] == 1
    assert data["summarizer"] is None or "state" in data["summarizer"]


def test_a_source_missing_its_credentials_is_called_out_by_variable_name(client, world):
    # Covers: FR-ADM-10
    world.sources_file.write_text(CONFIG.replace("enabled: false", "enabled: true"), encoding="utf-8")
    world.heartbeats.beat(Heartbeat(
        beat_at=NOW,
        credentials={"jira-product": {"KB_JIRA_BASE_URL": True, "KB_JIRA_PAT": False,
                                      "KB_JIRA_USERNAME": False, "KB_JIRA_PASSWORD": False,
                                      "JIRA_BASE_URL": False, "JIRA_PAT": False}},
    ))
    missing = client.get("/api/issues").json()["missing_credentials"]
    assert [m["source_id"] for m in missing] == ["jira-product"]
    assert missing[0]["variables"]["KB_JIRA_PAT"] is False


def test_the_gap_log_is_readable(client):
    # Covers: FR-ADM-10
    assert client.get("/api/gaps").json()["gaps"][0]["times"] == 3


def test_a_check_asks_each_dependency(client, world):
    # Covers: FR-ADM-05
    report = client.post("/api/check", headers=CSRF).json()
    assert report["checks"]["database"]["ok"] and report["checks"]["summarizer"]["ok"]
    assert report["checks"]["sources"]["enabled"] == ["confluence-eng"]
    assert report["ready"] is True

    world.summarizer.ok = False
    report = client.post("/api/check", headers=CSRF).json()
    assert report["ready"] is False and "system role" in report["checks"]["summarizer"]["error"]


# -- no secret ever leaves (FR-ADM-15) ------------------------------------------------------


def test_no_response_contains_a_credential_value(client, world, monkeypatch):
    # Covers: FR-ADM-15
    secret = "s3cret-value-that-must-not-leak"
    monkeypatch.setenv("KB_CONFLUENCE_PAT", secret)
    monkeypatch.setenv("KB_JIRA_PAT", secret)
    finished(world, created=1)
    world.queue.enqueue(SyncRequest(source_id="confluence-eng"), at=NOW)

    seen = []
    for path in ("/api/overview", "/api/sources", "/api/schedules", "/api/runs", "/api/runs/1",
                 "/api/issues", "/api/gaps", "/api/config", "/api/sync/requests", "/api/forms",
                 "/api/forms/x", "/api/forms/x/content"):
        seen.append(client.get(path).text)
    seen.append(client.post("/api/check", headers=CSRF).text)

    joined = "\n".join(seen)
    assert secret not in joined
    assert TOKEN not in joined, "nor the console's own token"
    # What is reported is only whether a variable is set.
    cards = client.get("/api/sources").json()["sources"]
    assert cards[0]["credentials"]["KB_CONFLUENCE_PAT"] is True


def test_an_unexpected_failure_says_so_without_leaking_its_detail(client, world):
    # Covers: FR-ADM-15
    def broken():
        raise RuntimeError("connection to db.internal with password hunter2 failed")

    world.store.status = broken
    response = client.get("/api/overview")
    assert response.status_code == 500 and "hunter2" not in response.text


# -- the single-page app -------------------------------------------------------------------


def test_the_built_app_is_served_with_a_fallback_for_client_side_routes(world, tmp_path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html>console</html>", encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    c = make_client(world, static_dir=str(web))

    assert "console" in c.get("/").text
    assert "console" in c.get("/sources").text, "a client-side route falls back to the app"
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/api/nonsense").status_code == 404 or c.get("/api/nonsense").status_code == 401
    # Nothing outside the app's directory is reachable.
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    assert "no" != c.get("/..%2fsecret.txt").text
    assert c.get("/../secret.txt").text.startswith("<html>")


# -- changing the source configuration (FR-CFG-10, FR-ADM-11, FR-ADM-16) ----------------------


def preview(client, change, **headers):
    return client.post("/api/sources/preview", headers={**CSRF, **headers}, json={"change": change})


def apply(client, change, etag, confirm=True):
    return client.post("/api/sources/apply", headers=CSRF, json={"change": change, "etag": etag, "confirm": confirm})


EDIT_SPACES = {"action": "update", "id": "confluence-eng", "config": {"spaces": ["ENG", "OPS"]}}


def test_the_sources_list_says_where_each_came_from_and_whether_it_can_be_edited(client, world):
    # Covers: FR-ADM-11
    data = client.get("/api/sources").json()
    assert data["editable"] is True
    assert {c["id"]: c["origin"] for c in data["sources"]} == {"confluence-eng": "yaml", "jira-product": "yaml"}


def test_a_preview_shows_the_diff_and_the_warnings_and_writes_nothing(client, world):
    # Covers: FR-ADM-11, FR-CFG-10
    before = world.sources_file.read_text(encoding="utf-8")
    body = preview(client, {"action": "update", "id": "confluence-eng", "config": {"store_raw_content": False}}).json()
    assert world.sources_file.read_text(encoding="utf-8") == before
    assert body["changed"] is True and "-    store_raw_content: true" in body["diff"]
    assert any("deletes all the text" in w["text"] for w in body["warnings"])
    assert body["etag"]

    risky = preview(client, {"action": "update", "id": "jira-product", "config": {"store_raw_content": True}}).json()
    assert any(w["level"] == "danger" and "every user of the catalog" in w["text"] for w in risky["warnings"])


def test_an_apply_changes_the_file_and_the_console_then_shows_it(client, world):
    # Covers: FR-ADM-11, FR-CFG-10, FR-CFG-12
    etag = preview(client, EDIT_SPACES).json()["etag"]
    response = apply(client, EDIT_SPACES, etag)
    assert response.status_code == 200 and response.json()["changed"] is True

    assert "spaces: [ENG, OPS]" in world.sources_file.read_text(encoding="utf-8")
    card = next(c for c in client.get("/api/sources").json()["sources"] if c["id"] == "confluence-eng")
    assert card["settings"]["spaces"] == ["ENG", "OPS"]
    history = client.get("/api/sources/revisions").json()["revisions"]
    assert history[0]["action"] == "update"
    assert history[0]["actor"].startswith("admin (") and "+    spaces: [ENG, OPS]" in history[0]["diff"]


def test_an_apply_needs_the_previews_etag_and_an_explicit_confirmation(client, world):
    # Covers: FR-CFG-10
    before = world.sources_file.read_text(encoding="utf-8")
    etag = preview(client, EDIT_SPACES).json()["etag"]
    assert apply(client, EDIT_SPACES, etag, confirm=False).status_code == 400
    assert client.post("/api/sources/apply", headers=CSRF, json={"change": EDIT_SPACES, "confirm": True}).status_code == 400
    assert world.sources_file.read_text(encoding="utf-8") == before


def test_a_file_changed_after_the_preview_is_a_conflict_not_an_overwrite(client, world):
    # Covers: FR-CFG-10
    etag = preview(client, EDIT_SPACES).json()["etag"]
    world.sources_file.write_text(world.sources_file.read_text(encoding="utf-8") + "# by hand\n", encoding="utf-8")
    response = apply(client, EDIT_SPACES, etag)
    assert response.status_code == 409 and response.json()["error"]["type"] == "conflict"
    assert world.sources_file.read_text(encoding="utf-8").endswith("# by hand\n")
    assert "OPS" not in world.sources_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("change, fragment", [
    ({"action": "create", "config": {"id": "form", "kind": "jira", "jql": "x"}}, "reserved"),
    ({"action": "create", "config": {"id": "n", "kind": "gitlab", "base_url": "https://g", "token_env": "glpat-secret123"}}, "token_env"),
    ({"action": "update", "id": "nope", "config": {}}, "no source"),
    ({"action": "explode"}, "not understood"),
    ({"nonsense": True}, "not understood"),
])
def test_an_invalid_change_is_refused_with_the_reason(client, world, change, fragment):
    # Covers: FR-CFG-10, FR-CFG-11
    before = world.sources_file.read_text(encoding="utf-8")
    response = client.post("/api/sources/preview", headers=CSRF, json={"change": change})
    assert response.status_code == 400 and fragment in response.json()["error"]["message"]
    assert world.sources_file.read_text(encoding="utf-8") == before


def test_a_new_source_can_be_added_and_is_marked_as_made_in_the_console(client, world):
    # Covers: FR-ADM-11, FR-CFG-12
    change = {"action": "create", "config": {"id": "wiki-ops", "kind": "confluence", "spaces": ["OPS"]}}
    etag = preview(client, change).json()["etag"]
    assert apply(client, change, etag).status_code == 200
    cards = {c["id"]: c for c in client.get("/api/sources").json()["sources"]}
    assert cards["wiki-ops"]["origin"] == "ui" and cards["wiki-ops"]["enabled"] is False
    assert cards["confluence-eng"]["origin"] == "yaml"


def test_syncing_can_be_switched_off_and_on_from_the_console(client, world):
    # Covers: FR-ADM-11, FR-CFG-05
    off = {"action": "approved", "value": False}
    body = preview(client, off).json()
    assert any(w["level"] == "danger" for w in body["warnings"])
    apply(client, off, body["etag"])
    assert client.get("/api/overview").json()["approved"] == {"value": False, "from": "file"}
    on = {"action": "approved", "value": True}
    apply(client, on, preview(client, on).json()["etag"])
    assert client.get("/api/overview").json()["approved"]["value"] is True


def test_a_hand_edit_of_the_file_is_noticed_and_recorded(client, world):
    # Covers: FR-CFG-12
    client.get("/api/sources")
    world.sources_file.write_text(world.sources_file.read_text(encoding="utf-8").replace("[ENG]", "[ENG, BIZ]"), encoding="utf-8")
    client.get("/api/sources")
    revisions = client.get("/api/sources/revisions").json()["revisions"]
    assert revisions[0]["action"] == "file" and revisions[0]["actor"] == "file"
    assert "+    spaces: [ENG, BIZ]" in revisions[0]["diff"]


def test_a_file_the_console_cannot_write_is_reported_not_silently_ignored(client, world, monkeypatch):
    # Covers: FR-ADM-11
    monkeypatch.setattr(FileSourcesFile, "writable", lambda self: False)
    assert client.get("/api/sources").json()["editable"] is False
    etag = preview(client, EDIT_SPACES).json()["etag"]
    response = apply(client, EDIT_SPACES, etag)
    assert response.status_code == 409 and response.json()["error"]["type"] == "not_writable"
    assert "OPS" not in world.sources_file.read_text(encoding="utf-8")


def test_editing_is_unavailable_when_the_console_is_not_wired_for_it(client, world):
    # Covers: FR-ADM-11
    world.sources_editor = None
    assert client.get("/api/sources").json()["editable"] is False
    assert preview(client, EDIT_SPACES).status_code == 400


def test_entries_of_a_source_that_is_no_longer_configured_are_listed_apart(client, world):
    # Covers: FR-ADM-16
    data = client.get("/api/sources").json()
    assert data["orphans"] == [{"source_id": "retired-wiki", "entries": 3}], "the manual records' own source is not an orphan"


def test_retiring_a_gone_sources_entries_is_a_request_to_the_scheduler(client, world):
    # Covers: FR-ADM-16
    response = client.post("/api/sources/retired-wiki/retire", headers=CSRF)
    assert response.status_code == 202
    [queued] = world.queue.requests
    assert (queued.kind.value, queued.source_id) == ("retire_source", "retired-wiki")
    assert client.post("/api/sources/no-such/retire", headers=CSRF).status_code == 400
    # Twice is once.
    client.post("/api/sources/retired-wiki/retire", headers=CSRF)
    assert len(world.queue.requests) == 1


def test_a_source_card_separates_what_it_says_from_what_it_inherits(client, world):
    # Covers: FR-ADM-11
    # The edit form needs it: saving must not copy every default into the file.
    world.sources_file.write_text(
        CONFIG.replace("    spaces: [ENG]\n", "    spaces: [ENG]\n    mechanisms:\n      incremental: { every: 5m }\n"),
        encoding="utf-8",
    )
    data = client.get("/api/sources").json()
    card = next(c for c in data["sources"] if c["id"] == "confluence-eng")
    assert card["own_mechanisms"] == {"incremental": {"every": "5m"}}
    assert card["mechanisms"]["incremental"] == {"enabled": True, "every": "5m"}
    assert data["defaults"]["mechanisms"]["incremental"] == {"enabled": True, "every": "15m"}
    other = next(c for c in data["sources"] if c["id"] == "jira-product")
    assert other["own_mechanisms"] == {}
