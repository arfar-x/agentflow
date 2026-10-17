"""An OpenAI-compatible endpoint that answers every summary the same way."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _reply(self, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # /models, for `kb check`
        self._reply({"data": [{"id": "stub"}]})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length))
        user = request["messages"][-1]["content"].strip()
        if user == "ping":  # the preflight probe
            content = '{"probe": "ok"}'
        else:
            # Answer about the document actually sent: its title and the start of its text.
            title = next((l[7:] for l in user.splitlines() if l.startswith("Title: ")), "Untitled")
            text = user.split("----\n", 1)[-1].strip().replace("\n", " ")[:80]
            fa = (
                "انتشار هر سه‌شنبه پس از توقف تغییرات انجام می‌شود."
                if "Release" in title
                else f"درباره {title}"
            )
            content = json.dumps(
                {
                    "title": {"en": title, "fa": f"عنوان {title}"},
                    "summary": {"en": f"About {title}: {text}", "fa": fa},
                    "keywords": [title.lower(), "stub"],
                }
            )
        self._reply({"choices": [{"message": {"content": content}}]})


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 18080), Handler).serve_forever()
