"""The admin console's HTTP API and single-page app.

`build_app` takes everything it touches as arguments, like `webhook.build_app`,
so each rule here is provable with a test client and in-memory fakes.

Handlers are `async` on purpose, though they call blocking database code: one
event-loop thread means one connection used by one caller at a time, which is
all an admin console needs and rules out two requests interleaving their
transactions on a shared connection. Anything slow that touches no database
(the summarizer, a model call) goes to a worker thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from pydantic import TypeAdapter, ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from kb.adapters.inbound.admin import views
from kb.adapters.inbound.admin.auth import COOKIE, Auth, csrf_ok, same_origin
from kb.adapters.outbound.source_factory import missing_credentials
from kb.application.ports.clock import Clock
from kb.application.ports.heartbeat import HeartbeatStore
from kb.application.ports.source_files import FileNotWritable, SourceChange
from kb.application.ports.run_log import RunLog
from kb.application.ports.sync_queue import RequestStatus, SyncQueue
from kb.application.use_cases.console_status import assess
from kb.application.use_cases.draft_summary import NothingToSummarize
from kb.application.use_cases.edit_sources import Conflict, InvalidChange, NotConfirmed
from kb.application.use_cases.manage_forms import (
    FormExists,
    FormInvalid,
    FormNotFound,
    publication_state,
)
from kb.application.use_cases.request_sync import InvalidRequest, RequestSync
from kb.application.use_cases.schedule import ScheduleState, next_due
from kb.config import AdminSettings
from kb.domain.content import DEFAULT_RAW_CONTENT_MAX_BYTES
from kb.domain.entry import EntryType
from kb.domain.identity import entry_id_for
from kb.domain.runs import RunStatus
from kb.sources_config import FORM_SOURCE_ID, ConfigError, SourcesConfig, load

logger = logging.getLogger("kb.admin")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class AdminServices:
    """Everything the console reads or asks for.

    `store` needs `status`, `top_misses`, `content_by_source` and
    `refresh_queue_summary` (the Postgres store has them; tests supply them).
    """

    store: Any
    runs: RunLog
    queue: SyncQueue
    heartbeats: HeartbeatStore
    clock: Clock
    sources_file: Path
    languages: tuple[str, ...] = ("en",)
    #: Has `check()`; None when no summarizer is configured.
    summarizer: Any = None
    #: `EditSources`; None when the console is not wired to edit the file.
    sources_editor: Any = None
    #: Manual records (FR-ADM-12..14): the records, `ManageForms`, `DraftSummary`
    #: (None without a summarizer) and a `GetEntry` that queues no refresh.
    forms: Any = None
    manage_forms: Any = None
    draft_summary: Any = None
    get_entry: Any = None
    extras: dict[str, Any] = field(default_factory=dict)


def _error(status: int, kind: str, message: str, **extra: Any) -> JSONResponse:
    return _json({"error": {"type": kind, "message": message, **extra}}, status)


class _Json(JSONResponse):
    def render(self, content: Any) -> bytes:
        return views.to_json(content).encode("utf-8")


def _json(content: Any, status: int = 200, **kwargs: Any) -> JSONResponse:
    return _Json(content, status_code=status, **kwargs)


async def _body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise InvalidRequest("the request body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidRequest("the request body must be a JSON object")
    return payload


def _flag(payload: Mapping[str, Any], name: str) -> bool:
    value = payload.get(name, False)
    if not isinstance(value, bool):
        raise InvalidRequest(f"{name!r} must be true or false")
    return value


def build_app(
    *,
    settings: AdminSettings,
    services: AdminServices,
    now: Callable[[], float] | None = None,
) -> Starlette:
    import time

    auth = Auth(settings.token, session_seconds=settings.session_hours * 3600, now=now or time.time)
    request_sync = RequestSync(services.queue, services.clock)
    static_dir = Path(settings.static_dir)

    # -- plumbing ------------------------------------------------------------
    def protected(handler: Callable[[Request], Awaitable[Response]]):
        async def wrapped(request: Request) -> Response:
            how = auth.authenticated(request)
            if how is None:
                return _error(401, "unauthorized", "log in with the admin token")
            # A bearer token is not ambient: a hostile page cannot make the
            # browser attach it. Only the cookie needs the CSRF check.
            if request.method not in SAFE_METHODS and how == "cookie" and not csrf_ok(request):
                return _error(403, "csrf", "this request did not come from the console")
            try:
                return await handler(request)
            except InvalidRequest as exc:
                return _error(400, "invalid_request", str(exc))
            except InvalidChange as exc:
                return _error(400, "invalid_change", str(exc))
            except NotConfirmed as exc:
                return _error(400, "not_confirmed", str(exc))
            except Conflict as exc:
                return _error(409, "conflict", str(exc))
            except FormInvalid as exc:
                return _error(400, "invalid_record", str(exc))
            except NothingToSummarize as exc:
                return _error(400, "nothing_to_summarize", str(exc))
            except FormExists as exc:
                return _error(409, "exists", str(exc))
            except FormNotFound:
                return _error(404, "not_found", "no such record")
            except FileNotWritable as exc:
                return _error(409, "not_writable", str(exc))
            except Exception:
                logger.exception("%s %s failed", request.method, request.url.path)
                return _error(500, "internal", "the console hit an unexpected error; see its log")

        return wrapped

    def config() -> tuple[SourcesConfig | None, str | None]:
        try:
            # Lenient: this process has no source credentials, so the variables
            # that carry them cannot be resolved here. The scheduler, which can,
            # is the authority on whether the file is complete.
            return load(services.sources_file, lenient=True), None
        except ConfigError as exc:
            return None, str(exc)

    def known_sources() -> set[str]:
        loaded, _ = config()
        # The built-in source is always there to be synced, configured or not.
        return ({s.id for s in loaded.sources} if loaded else set()) | {FORM_SOURCE_ID}

    # -- session -------------------------------------------------------------
    async def health(_: Request) -> Response:
        return _json({"status": "ok"})

    async def session_state(request: Request) -> Response:
        return _json({"authenticated": auth.authenticated(request) is not None})

    async def login(request: Request) -> Response:
        if not csrf_ok(request):
            return _error(403, "csrf", "this request did not come from the console")
        client = request.client.host if request.client else "unknown"
        wait = auth.locked_out(client)
        if wait:
            return _json(
                {"error": {"type": "too_many_attempts",
                           "message": f"too many failed logins; try again in {wait} s"}},
                429,
                headers={"Retry-After": str(wait)},
            )
        try:
            candidate = (await _body(request)).get("token", "")
        except InvalidRequest as exc:
            return _error(400, "invalid_request", str(exc))
        if not isinstance(candidate, str) or not auth.token_matches(candidate):
            auth.record_failure(client)
            return _error(401, "unauthorized", "that is not the admin token")
        auth.clear_failures(client)
        response = _json({"authenticated": True})
        response.set_cookie(
            COOKIE,
            auth.issue_cookie(),
            max_age=auth.ttl_seconds,
            httponly=True,
            samesite="strict",
            secure=settings.cookie_secure,
            path="/",
        )
        return response

    async def logout(request: Request) -> Response:
        if not same_origin(request):
            return _error(403, "csrf", "this request did not come from the console")
        response = _json({"authenticated": False})
        response.delete_cookie(COOKIE, path="/")
        return response

    # -- reads ---------------------------------------------------------------
    async def get_config(_: Request) -> Response:
        loaded, error = config()
        return _json(
            {
                "languages": list(services.languages),
                "summarizer_configured": services.summarizer is not None,
                "entry_types": [t.value for t in EntryType],
                "sources_file": services.sources_file.name,
                "config_error": error,
                "kinds": ["confluence", "jira", "gitlab"],
                "manual_records": services.manage_forms is not None,
            }
        )

    async def overview(_: Request) -> Response:
        now_ = services.clock.now()
        loaded, error = config()
        beat = services.heartbeats.last_beat()
        stats = services.runs.run_stats(since=now_ - timedelta(hours=24))
        queued = services.queue.list_requests(statuses=[RequestStatus.QUEUED], limit=200)
        running = services.queue.list_requests(statuses=[RequestStatus.RUNNING], limit=200)
        # The file is fresher than the scheduler's last tick: an operator who has
        # just switched syncing off should see it now, not at the next beat.
        effective = beat
        if beat is not None and loaded is not None:
            effective = beat.model_copy(
                update={"approved": loaded.approved, "approved_from": loaded.approved_from,
                        "config_error": None}
            )
        elif beat is not None and error is not None:
            effective = beat.model_copy(update={"config_error": error})
        assessment = assess(
            heartbeat=effective,
            now=now_,
            stats=stats,
            running_requests=len(running),
            sources_enabled=len(loaded.enabled_sources()) if loaded else 0,
        )
        status = services.store.status()
        last = services.runs.list_runs(limit=1)
        return _json(
            {
                "now": now_,
                "health": assessment.health,
                "problems": list(assessment.problems),
                "scheduler": views.heartbeat_view(beat, assessment.scheduler_alive),
                "approved": (
                    {"value": loaded.approved, "from": loaded.approved_from}
                    if loaded
                    else {"value": beat.approved if beat else True,
                          "from": beat.approved_from if beat else "default"}
                ),
                "config_error": error,
                "entries": status["entries"],
                "by_type": status["by_type"],
                "overrides": status["overrides"],
                "gaps": status["gaps"],
                "raw_content": status["raw_content"],
                "runs_24h": {**stats.model_dump(), "success_rate": stats.success_rate},
                "queue": {"queued": len(queued), "running": len(running)},
                "refresh_queue": services.store.refresh_queue_summary(),
                "sources": {
                    "configured": len(loaded.sources) if loaded else 0,
                    "enabled": len(loaded.enabled_sources()) if loaded else 0,
                },
                "last_run": views.run_summary(last[0] if last else None),
            }
        )

    def manual_card(entries: int | None = None) -> dict[str, Any] | None:
        """The built-in source, shown beside the configured ones."""
        if services.forms is None:
            return None
        records = services.forms.list()
        pending = sum(1 for r in records if publication_state(r, services.store) == "pending")
        if entries is None:
            entries = next(
                (row["entries"] for row in services.store.status()["by_source"]
                 if row["source_id"] == FORM_SOURCE_ID), 0,
            )
        latest = services.runs.latest_per_source().get(FORM_SOURCE_ID)
        return {
            "id": FORM_SOURCE_ID,
            "records": len(records),
            "pending": pending,
            "entries": entries,
            "last_run": views.run_summary(latest),
        }

    async def sources(_: Request) -> Response:
        editor = services.sources_editor
        if editor is not None:
            # Notice a hand edit of the file, and catch the copy up to it.
            editor.sync_mirror()
        origins = editor.origins() if editor is not None else {}
        loaded, error = config()
        if loaded is None:
            return _json(
                {"sources": [], "config_error": error, "editable": False, "orphans": [],
                 "manual": manual_card()}
            )
        status = services.store.status()
        entries = {row["source_id"]: row["entries"] for row in status["by_source"]}
        configured = {s.id for s in loaded.sources}
        orphans = [
            {"source_id": row["source_id"], "entries": row["entries"]}
            for row in status["by_source"]
            if row["source_id"] not in configured and row["source_id"] != FORM_SOURCE_ID
        ]
        content = services.store.content_by_source()
        latest = services.runs.latest_per_source()
        beat = services.heartbeats.last_beat()
        credentials = beat.credentials if beat else {}
        return _json(
            {
                "config_error": None,
                "editable": editor is not None and editor.writable(),
                "defaults": {
                    "mechanisms": views._without_empty(loaded.defaults.mechanisms.model_dump(exclude_none=True)),
                    "raw_content_max_bytes": loaded.defaults.raw_content_max_bytes,
                },
                "approved": {"value": loaded.approved, "from": loaded.approved_from},
                "orphans": orphans,
                "manual": manual_card(entries.get(FORM_SOURCE_ID, 0)),
                "sources": [
                    views.source_card(
                        source,
                        loaded,
                        entries=entries.get(source.id, 0),
                        content=content.get(source.id),
                        last_run=latest.get(source.id),
                        credentials=credentials.get(source.id),
                        origin=origins.get(source.id, "yaml"),
                    )
                    for source in loaded.sources
                ],
            }
        )

    async def schedules(_: Request) -> Response:
        loaded, error = config()
        if loaded is None:
            return _json({"rows": [], "config_error": error})
        now_ = services.clock.now()
        recent = services.runs.list_runs(limit=200)
        running_keys = frozenset(
            f"{r.mechanism}:{r.source_id}" for r in recent if r.status is RunStatus.RUNNING
        )
        state = ScheduleState(
            last_run=services.runs.last_scheduled(), since=now_, running=running_keys
        )
        newest: dict[tuple[str, str | None], Any] = {}
        for run in recent:  # newest first, so the first seen is the latest
            newest.setdefault((run.mechanism, None if run.source_id == "*" else run.source_id), run)
        rows = []
        for row in next_due(loaded, state, now_):
            result = newest.get((row.kind.value, row.source_id))
            rows.append({**row.model_dump(mode="json"), "last_result": views.run_summary(result)})
        return _json(
            {
                "now": now_,
                "approved": {"value": loaded.approved, "from": loaded.approved_from},
                "rows": rows,
                "config_error": None,
            }
        )

    async def runs_list(request: Request) -> Response:
        q = request.query_params
        try:
            status = RunStatus(q["status"]) if q.get("status") else None
            before = int(q["before"]) if q.get("before") else None
            limit = int(q.get("limit", 50))
        except ValueError as exc:
            raise InvalidRequest("status, before and limit must be a known status and numbers") from exc
        found = services.runs.list_runs(
            source_id=q.get("source") or None, status=status, mode=q.get("mode") or None,
            before_id=before, limit=limit,
        )
        limit = max(1, min(limit, 200))
        return _json(
            {
                "runs": [views.run_summary(r) for r in found],
                "next_before": found[-1].id if len(found) == limit else None,
            }
        )

    async def run_get(request: Request) -> Response:
        try:
            run_id = int(request.path_params["run_id"])
        except ValueError:
            return _error(404, "not_found", "no such run")
        run = services.runs.get_run(run_id)
        if run is None:
            return _error(404, "not_found", "no such run")
        return _json(views.run_detail(run))

    async def issues(_: Request) -> Response:
        loaded, error = config()
        beat = services.heartbeats.last_beat()
        problem_runs = [
            r
            for r in services.runs.list_runs(limit=100)
            if r.status in (RunStatus.ERROR, RunStatus.WARNING)
        ][:30]
        missing: list[dict[str, Any]] = []
        if loaded and beat:
            for source in loaded.enabled_sources():
                variables = beat.credentials.get(source.id, {})
                if variables and missing_credentials(source, variables):
                    missing.append({"source_id": source.id, "kind": source.kind, "variables": variables})
        return _json(
            {
                "config_error": error,
                "summarizer": (
                    {"state": beat.summarizer, "error": beat.summarizer_error} if beat else None
                ),
                "runs": [{**views.run_detail(r)} for r in problem_runs],
                "missing_credentials": missing,
                "refresh_queue": services.store.refresh_queue_summary(),
                "gaps": services.store.top_misses(limit=20),
            }
        )

    async def gaps(request: Request) -> Response:
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 200))
        except ValueError as exc:
            raise InvalidRequest("limit must be a number") from exc
        return _json({"gaps": services.store.top_misses(limit=limit)})

    async def check(_: Request) -> Response:
        """`kb check`'s question, asked of the same things the console depends on."""
        report: dict[str, Any] = {"ready": True, "checks": {}}

        def record(name: str, ok: bool, **detail: Any) -> None:
            report["checks"][name] = {"ok": ok, **detail}
            if not ok:
                report["ready"] = False

        try:
            status = services.store.status()
            record("database", True, entries=status["entries"], migrations=status["migrations"])
        except Exception as exc:
            record("database", False, error=str(exc).strip()[:300])
        if services.summarizer is None:
            record("summarizer", False, error="KB_SUMMARIZER_URL and KB_SUMMARIZER_MODEL are not both set")
        else:
            result = await run_in_threadpool(services.summarizer.check)
            record("summarizer", bool(result.get("ok")),
                   **{k: v for k, v in result.items() if k != "ok"})
        loaded, error = config()
        record("sources", loaded is not None, error=error,
               enabled=[s.id for s in loaded.enabled_sources()] if loaded else [])
        beat = services.heartbeats.last_beat()
        record("scheduler", bool(beat), error=None if beat else "the scheduler has never reported")
        return _json(report)

    # -- asking the scheduler --------------------------------------------------
    async def sync_requests(request: Request) -> Response:
        wanted = request.query_params.get("status")
        try:
            statuses = [RequestStatus(s) for s in wanted.split(",")] if wanted else None
        except ValueError as exc:
            raise InvalidRequest("status must be queued, running, done, failed or cancelled") from exc
        found = services.queue.list_requests(statuses=statuses, limit=50)
        waiting = sorted(r.id for r in found if r.status is RequestStatus.QUEUED)
        return _json(
            {
                "requests": [
                    {**r.model_dump(mode="json"),
                     "position": waiting.index(r.id) + 1 if r.id in waiting else None}
                    for r in found
                ]
            }
        )

    async def sync_create(request: Request) -> Response:
        payload = await _body(request)
        request_id = request_sync.sync(
            source_id=str(payload.get("source_id", "")),
            known_sources=known_sources(),
            mode=str(payload.get("mode", "incremental")),
            dry_run=_flag(payload, "dry_run"),
            forced=_flag(payload, "force"),
        )
        return _json({"request_id": request_id}, 202)

    async def sync_cancel(request: Request) -> Response:
        try:
            request_id = int(request.path_params["request_id"])
        except ValueError:
            return _error(404, "not_found", "no such request")
        if not request_sync.cancel(request_id):
            return _error(409, "already_started", "that request has already started or does not exist")
        return _json({"cancelled": request_id})

    async def purge_content(request: Request) -> Response:
        if request.path_params["source_id"] == FORM_SOURCE_ID:
            raise InvalidRequest("manual records are always kept readable: that is how agents read them")
        request_id = request_sync.purge_content(
            source_id=request.path_params["source_id"], known_sources=known_sources()
        )
        return _json({"request_id": request_id}, 202)

    # -- changing the source configuration (FR-CFG-10) ---------------------------
    def editor():
        if services.sources_editor is None:
            raise InvalidRequest("this console is not set up to edit the source configuration")
        return services.sources_editor

    def parse_change(payload: dict[str, Any]) -> SourceChange:
        try:
            return TypeAdapter(SourceChange).validate_python(payload.get("change"))
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(p) for p in d['loc'])}: {d['msg']}" for d in exc.errors())
            raise InvalidRequest(f"the change is not understood: {problems}") from exc

    async def sources_preview(request: Request) -> Response:
        change = parse_change(await _body(request))
        return _json(editor().preview(change))

    async def sources_apply(request: Request) -> Response:
        payload = await _body(request)
        change = parse_change(payload)
        etag = payload.get("etag")
        if not isinstance(etag, str) or not etag:
            raise InvalidRequest("apply the preview you were shown: send its etag")
        client = request.client.host if request.client else "unknown"
        applied = editor().apply(
            change, etag=etag, actor=f"admin ({client})", confirmed=payload.get("confirm") is True
        )
        return _json(applied)

    async def sources_revisions(request: Request) -> Response:
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 200))
        except ValueError as exc:
            raise InvalidRequest("limit must be a number") from exc
        editor().sync_mirror()
        return _json({"revisions": editor().revisions(limit=limit)})

    async def retire_source(request: Request) -> Response:
        if request.path_params["source_id"] == FORM_SOURCE_ID:
            raise InvalidRequest("manual records are retired by deleting them, one by one")
        live = {row["source_id"] for row in services.store.status()["by_source"]}
        request_id = request_sync.retire_source(
            source_id=request.path_params["source_id"], sources_with_entries=live
        )
        return _json({"request_id": request_id}, 202)

    # -- manual records (FR-ADM-12..14) --------------------------------------------
    def forms_service():
        if services.manage_forms is None:
            raise InvalidRequest("this console is not set up for manual records")
        return services.manage_forms

    def record_view(record: Any) -> dict[str, Any]:
        return {
            **record.model_dump(mode="json"),
            "publication": publication_state(record, services.store),
        }

    async def forms_list(_: Request) -> Response:
        forms_service()
        return _json(
            {
                "records": [record_view(r) for r in services.forms.list()],
                "languages": list(services.languages),
                "summarizer_configured": services.draft_summary is not None,
                "max_bytes": DEFAULT_RAW_CONTENT_MAX_BYTES,
            }
        )

    async def forms_create(request: Request) -> Response:
        saved = forms_service().create(await _body(request))
        return _json({"record": record_view(saved)}, 201)

    async def forms_get(request: Request) -> Response:
        forms_service()
        record = services.forms.get(request.path_params["slug"])
        if record is None:
            return _error(404, "not_found", "no such record")
        return _json({"record": record_view(record)})

    async def forms_update(request: Request) -> Response:
        saved = forms_service().update(request.path_params["slug"], await _body(request))
        return _json({"record": record_view(saved)})

    async def forms_delete(request: Request) -> Response:
        forms_service().delete(request.path_params["slug"])
        return _json({"deleted": request.path_params["slug"]})

    async def forms_draft_summary(request: Request) -> Response:
        forms_service()
        if services.draft_summary is None:
            return _error(
                409, "no_summarizer",
                "no summarizer is configured (KB_SUMMARIZER_URL and KB_SUMMARIZER_MODEL), "
                "so a summary has to be written by hand",
            )
        payload = await _body(request)
        title, body = payload.get("title", {}), payload.get("body", "")
        if not isinstance(title, dict) or not isinstance(body, str):
            raise InvalidRequest("title must be an object of languages and body a string")
        try:
            kind = EntryType(payload.get("type", "doc"))
        except ValueError as exc:
            raise InvalidRequest("type is not a known entry type") from exc
        try:
            # The model call is slow and touches no database: off the event loop.
            draft = await run_in_threadpool(
                lambda: services.draft_summary.execute(
                    title={str(k): str(v) for k, v in title.items()}, body=body, entry_type=kind
                )
            )
        except NothingToSummarize:
            raise
        except Exception:
            # What a model endpoint says back is not for the browser: it can echo
            # the prompt, the URL or a key. The log has it.
            logger.exception("draft summary failed")
            return _error(502, "summarizer_failed", "the summarizer did not answer; see the console's log")
        return _json(
            {
                "draft": draft.model_dump(mode="json"),
                # The port degrades to a bare title when the model fails (FR-REC-06),
                # so an empty summary means "nothing usable came back", not success.
                "usable": bool(draft.summary),
            }
        )

    async def forms_content(request: Request) -> Response:
        forms_service()
        slug = request.path_params["slug"]
        try:
            offset = int(request.query_params.get("offset", 0))
        except ValueError as exc:
            raise InvalidRequest("offset must be a number") from exc
        record = services.forms.get(slug)
        if record is None:
            return _error(404, "not_found", "no such record")
        view = services.get_entry.execute(entry_id_for(FORM_SOURCE_ID, slug), offset=offset)
        return _json(
            {
                "slug": slug,
                "publication": publication_state(record, services.store),
                # What `kb_get` would hand an agent -- None until the record is published.
                "content": view.content.model_dump(mode="json") if view and view.content else None,
                "entry": (
                    {"id": view.entry.id, "summary": view.entry.summary, "title": view.entry.title,
                     "hidden": view.hidden}
                    if view else None
                ),
            }
        )

    # -- the single-page app -------------------------------------------------
    async def spa(request: Request) -> Response:
        path = request.path_params.get("path", "")
        if path.startswith("api/") or path == "api":
            return _error(404, "not_found", "no such endpoint")
        if not static_dir.is_dir():
            return Response(
                "The console's web app is not built into this image. "
                "The API is available under /api.", media_type="text/plain", status_code=200,
            )
        root = static_dir.resolve()
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(root / "index.html")

    api = lambda handler: protected(handler)  # noqa: E731 - reads as a table below
    routes = [
        Route("/health", health),
        Route("/api/session", session_state, methods=["GET"]),
        Route("/api/session", login, methods=["POST"]),
        Route("/api/session", logout, methods=["DELETE"]),
        Route("/api/config", api(get_config)),
        Route("/api/overview", api(overview)),
        Route("/api/sources", api(sources)),
        Route("/api/sources/preview", api(sources_preview), methods=["POST"]),
        Route("/api/sources/apply", api(sources_apply), methods=["POST"]),
        Route("/api/sources/revisions", api(sources_revisions)),
        Route("/api/sources/{source_id}/purge-content", api(purge_content), methods=["POST"]),
        Route("/api/sources/{source_id}/retire", api(retire_source), methods=["POST"]),
        Route("/api/forms", api(forms_list), methods=["GET"]),
        Route("/api/forms", api(forms_create), methods=["POST"]),
        Route("/api/forms/draft-summary", api(forms_draft_summary), methods=["POST"]),
        Route("/api/forms/{slug}", api(forms_get), methods=["GET"]),
        Route("/api/forms/{slug}", api(forms_update), methods=["PUT"]),
        Route("/api/forms/{slug}", api(forms_delete), methods=["DELETE"]),
        Route("/api/forms/{slug}/content", api(forms_content)),
        Route("/api/schedules", api(schedules)),
        Route("/api/runs", api(runs_list)),
        Route("/api/runs/{run_id}", api(run_get)),
        Route("/api/issues", api(issues)),
        Route("/api/gaps", api(gaps)),
        Route("/api/check", api(check), methods=["POST"]),
        Route("/api/sync", api(sync_create), methods=["POST"]),
        Route("/api/sync/requests", api(sync_requests)),
        Route("/api/sync/requests/{request_id}", api(sync_cancel), methods=["DELETE"]),
        Route("/{path:path}", spa, methods=["GET"]),
    ]
    async def harden(request: Request, call_next):  # noqa: ANN001
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "frame-ancestors 'none'",
        )
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app = Starlette(routes=routes)
    app.add_middleware(BaseHTTPMiddleware, dispatch=harden)
    return app
