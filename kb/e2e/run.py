"""End to end: the admin console, the scheduler, a source, and the MCP server, for real.

Everything runs as its own process against a throwaway Postgres database:

    fake Confluence ── kb-scheduler (real, 1 s tick) ── Postgres ── kb-admin (real + built SPA)
    stub model      ──┘                                     └────── mcp-kb (real, as kb_reader)

Only the Confluence server and the model endpoint are stand-ins. The browser
drives the console (ui.mjs); this script starts everything, asks the questions a
browser cannot (the database, the file on disk, the MCP tools) and fails on the
first thing that is not as it should be.

    python e2e/run.py            # needs Postgres, Chromium, and `npm i` in kb/e2e

See e2e/README.md for the environment variables.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg
import requests
from psycopg.rows import dict_row

HERE = Path(__file__).resolve().parent
KB = HERE.parent
PY = sys.executable

PG_ADMIN_DSN = os.environ.get("E2E_PG_ADMIN_DSN", "postgresql://postgres@127.0.0.1:55432/postgres")
DB = "kb_e2e"
OWNER_DSN = PG_ADMIN_DSN.rsplit("/", 1)[0] + f"/{DB}"


def dsn_as(role: str, password: str) -> str:
    scheme, rest = OWNER_DSN.split("://", 1)
    return f"{scheme}://{role}:{password}@{rest.split('@', 1)[1]}"


ADMIN_PORT, MCP_PORT, WIKI_PORT, LLM_PORT = 8324, 18322, 18090, 18080
ADMIN_URL = f"http://127.0.0.1:{ADMIN_PORT}"
WIKI_URL = f"http://127.0.0.1:{WIKI_PORT}"
ADMIN_TOKEN = "k" * 40
WIKI_PAT = "pat-very-secret-1234"
READER_PW, ADMIN_PW = "reader-pw-e2e", "admin-pw-e2e"

WORK = Path(os.environ.get("E2E_WORK") or tempfile.mkdtemp(prefix="kb-e2e-"))
SOURCES = WORK / "kb-sources.yaml"

SOURCES_FIXTURE = """\
# kb-sources.yaml -- end-to-end fixture. The comments and the ${VAR} below must
# survive everything the console does to this file.
approved: true
defaults:
  mechanisms:
    incremental:  { enabled: true, every: 15m }
    full_scrape:  { enabled: false }
    lazy_refresh: { enabled: false }
sources:
  # The team's decision records; off until its token is set.
  - id: gitlab-adrs
    kind: gitlab
    enabled: false
    base_url: ${GITLAB_BASE_URL:-https://gitlab.invalid}
    token_env: GITLAB_TOKEN
    projects:
      - path: team/adrs
        scopes: [{ dir: ".", type: spec }]
"""

LONG = "<p>" + "Reconcile every ledger entry against the bank statement. " * 600 + "</p>"
PAGES = [
    {"id": 101, "title": "Payment reconciliation", "body": LONG, "version": 1},
    {"id": 102, "title": "Onboarding guide", "body": "<p>Welcome aboard. Laptops are issued on day one.</p>", "version": 1},
    {"id": 103, "title": "Old runbook", "body": "<p>Restart the batch node by hand.</p>", "version": 1},
]


# --------------------------------------------------------------------- plumbing
class Failure(AssertionError):
    pass


def step(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def check(condition, message: str) -> None:
    if not condition:
        raise Failure(message)
    print(f"  ok  {message}", flush=True)


def wait_for(fn, what: str, timeout: float = 60.0, every: float = 0.4):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            value = fn()
            if value:
                return value
        except Exception as exc:  # the thing may not be up yet
            last = exc
        time.sleep(every)
    raise Failure(f"timed out after {timeout:.0f}s waiting for {what}" + (f" ({last})" if last else ""))


class Proc:
    def __init__(self, name: str, argv: list[str], env: dict[str, str]) -> None:
        self.name, self.argv, self.env = name, argv, env
        self.log = WORK / f"{name}.log"
        self.process: subprocess.Popen | None = None

    def start(self) -> "Proc":
        handle = open(self.log, "ab")
        self.process = subprocess.Popen(
            self.argv, env=self.env, stdout=handle, stderr=subprocess.STDOUT, cwd=KB, start_new_session=True
        )
        return self

    def stop(self, sig: int = signal.SIGTERM) -> None:
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, sig)
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
        self.process = None


def base_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "VIRTUAL_ENV")}


def kb_env(role_dsn: str, **extra: str) -> dict[str, str]:
    env = base_env()
    env.update(
        KB_DATABASE_URL=role_dsn,
        KB_SOURCES_FILE=str(SOURCES),
        KB_SUMMARIZER_URL=f"http://127.0.0.1:{LLM_PORT}/v1",
        KB_SUMMARIZER_MODEL="stub",
        KB_SUMMARY_LANGUAGES="en,fa",
        KB_TIMEZONE="UTC",
    )
    env.update(extra)
    return env


# the scheduler is the only process that is handed source credentials
def scheduler_env(pat: str | None = WIKI_PAT) -> dict[str, str]:
    env = kb_env(OWNER_DSN, KB_CONFLUENCE_BASE_URL=WIKI_URL)
    if pat is not None:
        env["KB_CONFLUENCE_PAT"] = pat
    return env


def db():
    return psycopg.connect(OWNER_DSN, autocommit=True, row_factory=dict_row)


def q(sql: str, *params):
    with db() as conn:
        return conn.execute(sql, params).fetchall()


def admin(method: str, path: str, **kwargs):
    return requests.request(
        method, ADMIN_URL + path, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}, timeout=30, **kwargs
    )


def wiki(**state) -> None:
    requests.put(f"{WIKI_URL}/__state", json=state, timeout=5).raise_for_status()


def ui(phase: str, arg: str = "") -> None:
    env = {
        **base_env(),
        "E2E_URL": ADMIN_URL,
        "E2E_TOKEN": ADMIN_TOKEN,
        "E2E_OUT": str(WORK),
        "E2E_PLAYWRIGHT_FROM": os.environ.get("E2E_PLAYWRIGHT_FROM", str(HERE)),
    }
    result = subprocess.run(["node", str(HERE / "ui.mjs"), phase, arg], env=env, text=True, capture_output=True, timeout=240)
    print("".join(f"  | {line}\n" for line in result.stdout.splitlines()), end="", flush=True)
    if result.returncode:
        print(result.stderr)
        raise Failure(f"browser phase {phase!r} failed (screenshot: {WORK}/FAIL-{phase}.png)")


def request_sync(source: str, mode: str = "incremental", dry_run: bool = False, timeout: float = 90.0) -> dict:
    reply = admin("POST", "/api/sync", json={"source_id": source, "mode": mode, "dry_run": dry_run, "force": False})
    check(reply.status_code == 202, f"POST /api/sync {source} {mode}{' (dry)' if dry_run else ''} is accepted")
    request_id = reply.json()["request_id"]

    def settled():
        found = next(r for r in admin("GET", "/api/sync/requests").json()["requests"] if r["id"] == request_id)
        return found if found["status"] in ("done", "failed", "cancelled") else None

    return wait_for(settled, f"request {request_id} to finish", timeout)


def run_of(request: dict) -> dict:
    return admin("GET", f"/api/runs/{request['run_ids'][0]}").json()


async def _mcp(tool: str, arguments: dict) -> dict:
    from fastmcp import Client

    async with Client(f"http://127.0.0.1:{MCP_PORT}/mcp") as client:
        result = await client.call_tool(tool, arguments)
    return result.structured_content or json.loads(result.content[0].text)


def mcp(tool: str, **arguments) -> dict:
    return asyncio.run(_mcp(tool, arguments))


def search(*queries: str) -> list[dict]:
    return mcp("kb_search", queries=list(queries))["hits"]


# ----------------------------------------------------------------------- setup
def setup_database() -> None:
    from kb.adapters.outbound.postgres_store import PostgresEntryStore

    with psycopg.connect(PG_ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{DB}'")
        conn.execute(f"DROP DATABASE IF EXISTS {DB}")
        conn.execute(f"CREATE DATABASE {DB}")
    with psycopg.connect(OWNER_DSN, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        for role, password in (("kb_reader", READER_PW), ("kb_admin", ADMIN_PW)):
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            conn.execute(f"{'ALTER' if exists else 'CREATE'} ROLE {role} WITH LOGIN PASSWORD '{password}'")
    store = PostgresEntryStore.connect(OWNER_DSN)
    store.migrate()  # the reader's grants are part of the migrations
    with store.connection.cursor() as cursor:
        cursor.execute((KB / "grants" / "admin.sql").read_text(encoding="utf-8"))
    store.close()


def start_stack() -> dict[str, Proc]:
    procs = {
        "wiki": Proc("wiki", [PY, str(HERE / "fake_confluence.py"), str(WIKI_PORT), WIKI_PAT], base_env()),
        "llm": Proc("llm", [PY, str(HERE / "llm_stub.py")], base_env()),
        "scheduler": Proc("scheduler", [PY, str(HERE / "scheduler_fast.py")], scheduler_env()),
        "admin": Proc(
            "admin",
            [PY, "-m", "kb.adapters.inbound.admin"],
            # no source credentials: the console must work without them
            kb_env(
                dsn_as("kb_admin", ADMIN_PW),
                KB_ADMIN_TOKEN=ADMIN_TOKEN,
                KB_ADMIN_HOST="127.0.0.1",
                KB_ADMIN_PORT=str(ADMIN_PORT),
                KB_ADMIN_STATIC_DIR=str(KB / "frontend" / "dist"),
            ),
        ),
        "mcp": Proc(
            "mcp",
            [PY, "-m", "kb.adapters.inbound.mcp_server"],
            kb_env(dsn_as("kb_reader", READER_PW), KB_MCP_HOST="127.0.0.1", KB_MCP_PORT=str(MCP_PORT)),
        ),
    }
    for name in ("wiki", "llm"):
        procs[name].start()
    wait_for(lambda: requests.get(f"{WIKI_URL}/__state", timeout=2).ok, "the fake wiki")
    wiki(pages=PAGES)
    for name in ("scheduler", "admin", "mcp"):
        procs[name].start()
    wait_for(lambda: requests.get(f"{ADMIN_URL}/health", timeout=2).ok, "the console", 30)
    wait_for(lambda: q("SELECT 1 FROM scheduler_heartbeat"), "the scheduler's first heartbeat", 30)
    wait_for(lambda: _mcp_up(), "the MCP server", 30)
    return procs


def _mcp_up() -> bool:
    mcp("kb_search", queries=["warmup"])
    return True


# -------------------------------------------------------------------- scenarios
def scenario_login_and_guards() -> None:
    step("1. Sign in, and what is refused")
    check(requests.get(f"{ADMIN_URL}/api/overview", timeout=5).status_code == 401, "no credentials: 401")
    check(
        requests.post(f"{ADMIN_URL}/api/sync", json={}, headers={"Cookie": "x=y"}, timeout=5).status_code == 401,
        "a write with no session: 401",
    )
    ui("login")
    overview = admin("GET", "/api/overview").json()
    check(overview["scheduler"]["alive"] is True, "the overview reports a live scheduler")
    check(overview["approved"]["value"] is True, "syncing is on")


def scenario_add_source() -> None:
    step("2. Add a source from the console")
    before = SOURCES.read_text(encoding="utf-8")
    ui("add_source")
    after = SOURCES.read_text(encoding="utf-8")
    check("id: eng-wiki" in after, "the file now declares eng-wiki")
    check("# The team's decision records; off until its token is set." in after, "the operator's comment survived")
    check("${GITLAB_BASE_URL:-https://gitlab.invalid}" in after, "the ${VAR} placeholder survived, unresolved")
    check(before.splitlines()[0] == after.splitlines()[0], "the header is untouched")
    rows = q("SELECT action, source_id FROM kb_source_revision ORDER BY id")
    check([(r["action"], r["source_id"]) for r in rows][-1] == ("create", "eng-wiki"), "the change is in the revision history")
    check(q("SELECT enabled FROM kb_source WHERE id = 'eng-wiki'")[0]["enabled"] is False, "the DB mirror has it, disabled")
    check(q("SELECT count(*) AS n FROM entry")[0]["n"] == 0, "nothing was synced by adding it")


def scenario_enable_and_sync() -> None:
    step("3. Enable it, keep its text, and sync from the console")
    ui("enable_store")
    text = SOURCES.read_text(encoding="utf-8")
    check("store_raw_content: true" in text and re.search(r"id: eng-wiki[\s\S]*enabled: true", text), "enabled, keeping text, on disk")

    first = wait_for(
        lambda: q("SELECT * FROM sync_run WHERE source_id = 'eng-wiki' AND triggered_by = 'scheduled' AND status <> 'running'"),
        "the scheduler to sync the newly enabled source on its own",
        60,
    )[0]
    check((first["created"], first["content_stored"], first["status"]) == (3, 3, "success"), "enabling a source starts its first sync by itself: 3 created, 3 texts kept")

    pages = [dict(p) for p in PAGES]
    pages[1].update(version=2, body="<p>Welcome aboard. Laptops are issued on day one. Badges come from the front desk.</p>")
    wiki(pages=pages)
    ui("trigger", "dry")
    dry = q("SELECT * FROM sync_run WHERE source_id = 'eng-wiki' ORDER BY id")[-1]
    check((dry["dry_run"], dry["triggered_by"], dry["status"]) == (True, "manual", "success"), "the dry run is recorded as manual and successful")
    check(dry["seen"] == 3 and dry["updated"] == 0, "it saw the three pages and wrote nothing")
    check(q("SELECT source_version FROM entry WHERE id = 'eng-wiki:102'")[0]["source_version"] == "1", "the edited page's entry is still at version 1")

    ui("trigger", "full")
    run = q("SELECT * FROM sync_run WHERE source_id = 'eng-wiki' ORDER BY id")[-1]
    check((run["updated"], run["unchanged"], run["status"]) == (1, 2, "success"), "the full sync updated the one page that changed and left two alone")
    check(q("SELECT source_version FROM entry WHERE id = 'eng-wiki:102'")[0]["source_version"] == "2", "the entry is now at version 2")
    ui("reports")


def scenario_mcp() -> None:
    step("4. What an agent sees, through the real MCP server")
    hits = search("Payment reconciliation")
    check(any("Payment reconciliation" in json.dumps(h, ensure_ascii=False) for h in hits), "kb_search finds the page by title")
    entry_id = next(h["id"] for h in hits if "Payment reconciliation" in json.dumps(h, ensure_ascii=False))
    first = mcp("kb_get", id=entry_id)
    content = first["content"]
    check(content and content["total_chars"] > 24000 and content["next_offset"], "kb_get pages a long document and says where the next page starts")
    second = mcp("kb_get", id=entry_id, offset=content["next_offset"])["content"]
    check(len(content["text"]) + len(second["text"]) >= content["total_chars"] - 1 and second["next_offset"] is None, "the pages add up to the whole text")

    step("5. The wiki changes; the catalog follows")
    pages = [dict(p) for p in PAGES]
    pages[0].update(version=2, body="<p>Quarterly audit applies.</p>" + LONG)
    pages[1].update(version=2, body="<p>Welcome aboard. Laptops are issued on day one. Badges come from the front desk.</p>")
    wiki(pages=pages[:2])  # page 103 is deleted
    done = request_sync("eng-wiki", "incremental")
    check(done["status"] == "done", "an incremental sync runs")
    check("Quarterly audit applies" in mcp("kb_get", id=entry_id)["content"]["text"], "the edited page's new text is what kb_get returns")
    check(q("SELECT deleted_at FROM entry WHERE id = 'eng-wiki:103'")[0]["deleted_at"] is None, "an incremental pass does not conclude a page is gone")
    done = request_sync("eng-wiki", "full")
    run = run_of(done)
    check(run["missing"] == 1, "a full pass retires the deleted page")
    check(all("Old runbook" not in json.dumps(h) for h in search("Old runbook")), "and it no longer appears in search")


def scenario_failures() -> None:
    step("6. Failure paths")
    wiki(fail=True)
    done = request_sync("eng-wiki", "full")
    run = run_of(done)
    check(run["status"] == "error" and run["error"], f"a broken wiki is recorded as an error run ({run['error'][:50]!r})")
    check(done["status"] == "failed", "and the request is marked failed")
    ui("issues", "eng-wiki")
    wiki(fail=False)
    check(request_sync("eng-wiki", "full")["status"] == "done", "the next request succeeds once the wiki is back")
    check(q("SELECT count(*) AS n FROM entry WHERE deleted_at IS NULL")[0]["n"] == 2, "no entry was lost to the failure")

    step("6b. A scheduler with no credential")
    PROCS["scheduler"].stop()
    PROCS["scheduler"] = Proc("scheduler", [PY, str(HERE / "scheduler_fast.py")], scheduler_env(pat=None)).start()
    issues = wait_for(
        lambda: (r := admin("GET", "/api/issues").json())["missing_credentials"] and r,
        "the console to notice the missing credential",
        30,
    )
    missing = issues["missing_credentials"][0]
    check(missing["source_id"] == "eng-wiki" and missing["variables"].get("KB_CONFLUENCE_PAT") is False, "Issues names the variable that is not set")
    check(WIKI_PAT not in json.dumps(issues), "and never a value")
    PROCS["scheduler"].stop()
    PROCS["scheduler"] = Proc("scheduler", [PY, str(HERE / "scheduler_fast.py")], scheduler_env()).start()
    wait_for(lambda: not admin("GET", "/api/issues").json()["missing_credentials"], "the credential to be seen again", 30)


def scenario_forms() -> None:
    step("7. Manual records")
    search("release process")  # nothing yet: this logs the gap the editor starts from
    check(not search("release process"), "nothing answers 'release process' yet")
    ui("forms_create")
    hits = search("انتشار")
    check("form:release-process" in [h["id"] for h in hits], "a Persian search finds the record")
    entry_id = "form:release-process"
    got = mcp("kb_get", id=entry_id)
    check(got["entry"]["location"]["kind"] == "form" and "Tuesdays" in got["content"]["text"], "kb_get reads the record's text")
    ui("forms_edit_summary")
    check(mcp("kb_get", id=entry_id)["entry"]["summary"]["en"] == "Edited by hand: we ship on Tuesdays.", "the new summary reached the entry")
    calls = [r for r in q("SELECT 1 FROM sync_run WHERE source_id = 'form'")]
    check(len(calls) >= 2, "each save was a recorded run of the built-in source")
    ui("forms_delete")
    wait_for(lambda: q("SELECT deleted_at FROM entry WHERE id = 'form:release-process'")[0]["deleted_at"], "the entry to be retired")
    check("form:release-process" not in [h["id"] for h in search("انتشار")], "and it leaves search")


def scenario_kill_switch() -> None:
    step("8. The syncing switch")
    ui("approved_off")
    done = request_sync("eng-wiki", "full")
    check(done["status"] == "failed" and "switched off" in (done["error"] or ""), f"a requested sync is refused, saying why ({done['error']!r})")
    check(q("SELECT count(*) AS n FROM entry WHERE source_id = 'eng-wiki'")[0]["n"] == 3, "and touched nothing")
    created = admin("POST", "/api/forms", json={"slug": "while-off", "title": {"en": "Written while syncing is off"}, "body": "Still publishable.", "type": "doc"})
    check(created.status_code == 201, "a record can still be saved")
    wait_for(lambda: q("SELECT 1 FROM entry WHERE id = 'form:while-off' AND deleted_at IS NULL"), "it to be published anyway", 30)
    admin("DELETE", "/api/forms/while-off")
    change = {"action": "approved", "value": True}
    preview = admin("POST", "/api/sources/preview", json={"change": change}).json()
    check(admin("POST", "/api/sources/apply", json={"change": change, "etag": preview["etag"], "confirm": True}).ok, "syncing is switched back on")


def scenario_hand_edit() -> None:
    step("9. The file is edited by hand")
    change = {"action": "update", "id": "eng-wiki", "config": {"spaces": ["ENG", "OPS"]}}
    preview = admin("POST", "/api/sources/preview", json={"change": change}).json()
    SOURCES.write_text(SOURCES.read_text(encoding="utf-8") + "\n# edited by hand between preview and apply\n", encoding="utf-8")
    stale = admin("POST", "/api/sources/apply", json={"change": change, "etag": preview["etag"], "confirm": True})
    check(stale.status_code == 409, "applying a stale preview is refused (409)")
    check("OPS" not in SOURCES.read_text(encoding="utf-8"), "and nothing was written")
    admin("GET", "/api/sources")  # the console notices the hand edit
    check(q("SELECT 1 FROM kb_source_revision WHERE action = 'file'"), "the hand edit is recorded as a revision")
    ui("history")


def scenario_delete_source() -> None:
    step("10. Delete the source, and its entries with it")
    live = q("SELECT count(*) AS n FROM entry WHERE source_id = 'eng-wiki' AND deleted_at IS NULL")[0]["n"]
    check(live == 2, f"{live} live entries come from eng-wiki before")
    ui("delete_source")
    check("id: eng-wiki" not in SOURCES.read_text(encoding="utf-8"), "the file no longer declares it")
    check("id: gitlab-adrs" in SOURCES.read_text(encoding="utf-8"), "the other source is untouched")
    wait_for(
        lambda: q("SELECT count(*) AS n FROM entry WHERE source_id = 'eng-wiki' AND deleted_at IS NULL")[0]["n"] == 0,
        "the scheduler to retire its entries",
        30,
    )
    check(q("SELECT count(*) AS n FROM entry_content WHERE entry_id LIKE 'eng-wiki:%%'")[0]["n"] == 0, "their kept text went with them")
    check(not [h for h in search("Onboarding guide") if "Onboarding guide" in json.dumps(h)], "nothing from it is searchable")
    check(admin("GET", "/api/sources").json()["orphans"] == [], "the console lists no orphans")


def scenario_restart() -> None:
    step("11. The scheduler dies mid-run")
    # put the source back, so there is something to be in the middle of
    change = {"action": "create", "config": {"id": "eng-wiki", "kind": "confluence", "enabled": True, "spaces": ["ENG"]}}
    preview = admin("POST", "/api/sources/preview", json={"change": change}).json()
    check(admin("POST", "/api/sources/apply", json={"change": change, "etag": preview["etag"], "confirm": True}).ok, "eng-wiki is declared again")
    wiki(delay=30)
    reply = admin("POST", "/api/sync", json={"source_id": "eng-wiki", "mode": "full", "dry_run": False, "force": False})
    request_id = reply.json()["request_id"]
    wait_for(lambda: q("SELECT 1 FROM sync_run WHERE status = 'running'"), "a run to be in progress", 30)
    PROCS["scheduler"].stop(signal.SIGKILL)
    wiki(delay=0)
    scheduled_before = q("SELECT count(*) AS n FROM sync_run WHERE triggered_by = 'scheduled'")[0]["n"]
    PROCS["scheduler"] = Proc("scheduler", [PY, str(HERE / "scheduler_fast.py")], scheduler_env()).start()
    wait_for(lambda: not q("SELECT 1 FROM sync_run WHERE status = 'running'"), "the dead run to be closed", 30)
    run = q("SELECT status, error FROM sync_run WHERE source_id = 'eng-wiki' ORDER BY id DESC LIMIT 1")[0]
    check(run["status"] == "error" and "interrupted" in (run["error"] or "").lower(), f"it is closed as interrupted ({run['error']!r})")
    req = next(r for r in admin("GET", "/api/sync/requests").json()["requests"] if r["id"] == request_id)
    check(req["status"] == "failed", "and its request is failed, not left running")
    time.sleep(6)
    check(
        q("SELECT count(*) AS n FROM sync_run WHERE triggered_by = 'scheduled'")[0]["n"] == scheduled_before,
        "a restart does not make every cadence due at once",
    )
    check(request_sync("eng-wiki", "full")["status"] == "done", "and the next request runs normally")


def scenario_secrets_and_lockout() -> None:
    step("12. No credential reached a browser or a log")
    secrets = [WIKI_PAT, ADMIN_PW, READER_PW]
    captured = (WORK / "responses.jsonl").read_text(encoding="utf-8")
    check(len(captured.splitlines()) > 50, f"{len(captured.splitlines())} API responses were captured from the browser")
    for secret in secrets:
        check(secret not in captured, f"{secret[:6]}… is in none of them")
    log = (WORK / "admin.log").read_text(encoding="utf-8", errors="replace")
    for secret in secrets + [ADMIN_TOKEN]:
        check(secret not in log, f"{secret[:6]}… is not in the console's log")
    env = open(f"/proc/{PROCS['admin'].process.pid}/environ", "rb").read().decode().split("\0")
    check(not [e for e in env if e.startswith(("KB_CONFLUENCE", "KB_JIRA", "GITLAB", "KB_GITLAB"))], "the console process was never given a source credential")

    step("13. Guessing the token")
    codes = [
        requests.post(f"{ADMIN_URL}/api/session", json={"token": "wrong" * 10}, headers={"X-KB-Admin": "1"}, timeout=5).status_code
        for _ in range(7)
    ]
    check(codes[:5] == [401] * 5 and codes[-1] == 429, f"five wrong tokens, then locked out ({codes})")
    check(admin("GET", "/api/overview").ok, "while a script holding the token (Bearer) is unaffected")


SCENARIOS = [
    scenario_login_and_guards,
    scenario_add_source,
    scenario_enable_and_sync,
    scenario_mcp,
    scenario_failures,
    scenario_forms,
    scenario_kill_switch,
    scenario_hand_edit,
    scenario_delete_source,
    scenario_restart,
    scenario_secrets_and_lockout,
]
PROCS: dict[str, Proc] = {}


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    SOURCES.write_text(SOURCES_FIXTURE, encoding="utf-8")
    print(f"work dir: {WORK}")
    only = sys.argv[1:]
    try:
        setup_database()
        PROCS.update(start_stack())
        for scenario in SCENARIOS:
            if only and not any(name in scenario.__name__ for name in only):
                continue
            scenario()
    except Failure as failure:
        print(f"\nFAILED: {failure}", file=sys.stderr)
        print(f"logs and screenshots: {WORK}", file=sys.stderr)
        return 1
    finally:
        for proc in PROCS.values():
            proc.stop()
    print(f"\nALL SCENARIOS PASSED  (screenshots and logs: {WORK})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
