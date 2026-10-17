"""A Confluence Server look-alike, just big enough for `ConfluenceKnowledgeSource`.

It answers the two calls the source makes (`content/search`, `content/{id}`),
insists on the bearer token, and has a small control surface so a scenario can
edit, remove or break pages while the stack is running:

    PUT  /__state   {"pages": [...], "delay": 0, "fail": false}
    GET  /__state
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STATE = {"pages": [], "delay": 0.0, "fail": False, "token": "", "calls": 0}
LOCK = threading.Lock()


def page_json(page: dict) -> dict:
    return {
        "id": str(page["id"]),
        "title": page["title"],
        "space": {"key": page.get("space", "ENG")},
        "version": {"number": page.get("version", 1), "when": "2026-10-01T10:00:00.000Z"},
        "history": {"createdDate": "2026-09-01T10:00:00.000Z"},
        "metadata": {"labels": {"results": [{"name": label} for label in page.get("labels", [])]}},
        "body": {"storage": {"value": page["body"]}},
        "_links": {"webui": f"/pages/{page['id']}"},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # quiet
        pass

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self) -> None:
        if self.path != "/__state":
            return self._send(404, {})
        length = int(self.headers.get("Content-Length", 0))
        update = json.loads(self.rfile.read(length) or b"{}")
        with LOCK:
            STATE.update(update)
        self._send(200, {"ok": True})

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/__state":
            with LOCK:
                return self._send(200, {**STATE, "pages": len(STATE["pages"])})
        with LOCK:
            STATE["calls"] += 1
            delay, fail, token, pages = STATE["delay"], STATE["fail"], STATE["token"], list(STATE["pages"])
        if self.headers.get("Authorization") != f"Bearer {token}":
            return self._send(401, {"message": "bad credentials"})
        if delay:
            time.sleep(delay)
        if fail:
            return self._send(500, {"message": "the wiki is having a bad day"})
        if url.path == "/rest/api/content/search":
            query = parse_qs(url.query)
            start, limit = int(query.get("start", ["0"])[0]), int(query.get("limit", ["50"])[0])
            chunk = pages[start : start + limit]
            return self._send(200, {"results": [page_json(p) for p in chunk], "size": len(chunk)})
        if url.path.startswith("/rest/api/content/"):
            wanted = url.path.rsplit("/", 1)[1]
            for page in pages:
                if str(page["id"]) == wanted:
                    return self._send(200, page_json(page))
            return self._send(404, {"message": "no such page"})
        self._send(404, {})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18090
    STATE["token"] = sys.argv[2] if len(sys.argv) > 2 else "token"
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
