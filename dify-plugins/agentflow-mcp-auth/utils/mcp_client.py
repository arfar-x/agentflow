"""Minimal MCP (Model Context Protocol) client for the streamable-HTTP
transport, scoped to exactly what this plugin needs: talk to one fixed
server (agentflow's mcp-agent-skills), call ``tools/list`` and
``tools/call``, and let the caller attach a fresh set of headers -- the
per-user ``X-Agent-Skills-Env-*`` credential override -- on every single
call.

Deliberately not the full `mcp` SDK: that SDK's streamable-http client is
built around a long-lived, stateful async session, which doesn't fit this
plugin's one-shot-per-tool-invocation model. This mirrors the same
hand-rolled JSON-RPC-over-HTTP approach the community's own
`dify-plugin-tools-mcp_sse` plugin uses for the same reason, trimmed down
to streamable-HTTP only (agent-skills never speaks SSE) and with per-call
(not per-client) headers, since every call here may be a different end
user.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from dify_plugin.config.logger_format import plugin_logger_handler

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "agentflow-mcp-auth", "version": "0.1.0"}


class McpError(Exception):
    """Raised for any MCP-level error response or transport failure."""


class McpStreamableHttpClient:
    """One short-lived connection to a streamable-HTTP MCP server.

    Instantiate, call :meth:`initialize` once, then :meth:`list_tools` /
    :meth:`call_tool` as needed, then :meth:`close`. Not thread-safe and
    not meant to be kept around across plugin invocations -- a fresh
    instance (and therefore a fresh MCP session) is created for every
    single tool call, which is the right cost to pay for "this call
    carries this one user's own credentials and no other user's."
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None, timeout: float = 60.0):
        self._url = url
        self._client = httpx.Client(timeout=httpx.Timeout(timeout))
        self._base_headers = dict(headers or {})
        self._session_id: str | None = None
        self._next_id = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "McpStreamableHttpClient":
        self.initialize()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            **self._base_headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        try:
            response = self._client.post(self._url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise McpError(f"could not reach MCP server: {exc}") from exc

        if not response.is_success:
            raise McpError(f"MCP server returned HTTP {response.status_code}: {response.text[:500]}")

        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id

        if "id" not in payload:
            # A notification (e.g. notifications/initialized) has no response body.
            return {}

        if not response.content:
            return {}

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            message: dict[str, Any] = {}
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    message = json.loads(line[len("data:") :].strip())
            return message
        if "application/json" in content_type:
            return response.json()
        raise McpError(f"unsupported MCP response content-type: {content_type!r}")

    def initialize(self) -> None:
        response = self._send(
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
            }
        )
        if "error" in response:
            raise McpError(f"MCP initialize failed: {response['error']}")
        # Notification -- the server doesn't reply with a JSON-RPC id for this one.
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def list_tools(self) -> list[dict[str, Any]]:
        response = self._send({"jsonrpc": "2.0", "id": self._id(), "method": "tools/list", "params": {}})
        if "error" in response:
            raise McpError(f"MCP tools/list failed: {response['error']}")
        return response.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self._send(
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        if "error" in response:
            raise McpError(f"MCP tools/call({name}) failed: {response['error']}")
        return response.get("result", {})
