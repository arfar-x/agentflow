"""GitLab push events: the seconds-fresh mechanism.

A push to a watched repository refreshes only the files in its payload, within
seconds, instead of waiting for the next incremental tick.

This is the one component that accepts an inbound connection, so it is
deliberately the smallest thing in the module:

- **It runs on its own, on a private interface** (NFR-DEP-04), never inside
  `mcp-kb`, which keeps its no-published-port rule.
- **Every request must carry the shared secret.** GitLab sends it as
  `X-Gitlab-Token`; a request without it is rejected before the body is even
  parsed, and the comparison is constant-time.
- **It does no work of its own.** A valid event enqueues the changed paths and
  returns; the scheduler drains that queue with the credentials and the
  writable role. So the worst a forged, authenticated-looking request can do is
  ask for a re-read of documents that already exist.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger("kb.webhook")

#: A push event carries every commit; one merge can touch hundreds of files.
#: Beyond this, the incremental tick is the cheaper way to catch up.
MAX_PATHS_PER_EVENT = 200


def changed_paths(payload: dict[str, Any]) -> list[str]:
    """Every path added or modified by a push, without duplicates.

    Removals are ignored on purpose: only a full pass may conclude a document
    is gone, and a file deleted on a branch is not deleted on the default one.
    """
    seen: dict[str, None] = {}
    for commit in payload.get("commits") or []:
        for key in ("added", "modified"):
            for path in commit.get(key) or []:
                if isinstance(path, str) and path:
                    seen.setdefault(path, None)
    return list(seen)[:MAX_PATHS_PER_EVENT]


def build_app(
    *,
    secret: str,
    enqueue: Callable[[str, str], bool],
    source_ids_for_project: Callable[[str], list[str]],
) -> Starlette:
    """`enqueue(source_id, external_id) -> bool` returns whether the path is
    actually catalogued by that source; `source_ids_for_project` maps a GitLab
    project path to the sources watching it."""

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def gitlab(request: Request) -> JSONResponse:
        token = request.headers.get("x-gitlab-token", "")
        if not secret or not hmac.compare_digest(token, secret):
            # Before parsing anything: an unauthenticated caller should not be
            # able to make this process do work, however small.
            logger.warning("rejected a webhook with a bad or missing token")
            return JSONResponse({"error": "invalid token"}, status_code=401)

        event = request.headers.get("x-gitlab-event", "")
        if event and event != "Push Hook":
            # Tag pushes, merge requests, pipelines: acknowledged and ignored,
            # so GitLab does not mark the hook as failing.
            return JSONResponse({"ignored": event})

        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "malformed json"}, status_code=400)

        project = (payload.get("project") or {}).get("path_with_namespace") or ""
        sources = source_ids_for_project(project) if project else []
        if not sources:
            # A repository nothing is configured to watch. Not an error: hooks
            # outlive the configuration that justified them.
            return JSONResponse({"project": project, "watching": False, "queued": 0})

        paths = changed_paths(payload)
        queued = 0
        for source_id in sources:
            for path in paths:
                if enqueue(source_id, f"{project}:{path}"):
                    queued += 1

        logger.info("push to %s: queued %d of %d changed paths", project, queued, len(paths))
        return JSONResponse(
            {"project": project, "watching": True, "changed": len(paths), "queued": queued}
        )

    return Starlette(routes=[Route("/health", health), Route("/gitlab", gitlab, methods=["POST"])])


def main() -> None:  # pragma: no cover -- the container's entrypoint
    import logging as _logging
    import os

    import uvicorn

    from kb.config import Settings
    from kb.container import build
    from kb.sources_config import GitLabSource, load

    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    secret = os.environ.get("KB_WEBHOOK_SECRET", "")
    if not secret:
        raise SystemExit("KB_WEBHOOK_SECRET is not set -- refusing to accept unauthenticated webhooks")

    settings = Settings.from_env()
    container = build(settings)
    config = load(settings.sources_file)

    def sources_for(project: str) -> list[str]:
        ids = []
        for source in config.enabled_sources():
            if isinstance(source, GitLabSource) and any(p.path == project for p in source.projects):
                ids.append(source.id)
        return ids

    def enqueue(source_id: str, external_id: str) -> bool:
        from kb.domain.identity import entry_id_for

        entry_id = entry_id_for(source_id, external_id)
        if container.store.get(entry_id) is None:
            # Not catalogued: a new file is picked up by the next incremental
            # run, which knows the scopes. The webhook only refreshes what the
            # catalog already has.
            return False
        container.store.queue_refresh(entry_id, at=container.clock.now())
        return True

    app = build_app(secret=secret, enqueue=enqueue, source_ids_for_project=sources_for)
    uvicorn.run(
        app,
        host=os.environ.get("KB_WEBHOOK_HOST", "127.0.0.1"),
        port=int(os.environ.get("KB_WEBHOOK_PORT", "8323")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
