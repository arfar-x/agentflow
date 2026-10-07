"""Loads this plugin's deployment-specific config from config/toolsets.json
and exposes it as typed Python values.

Keeping this in a JSON file rather than inline Python means a fork that
renames a service, changes a toolset's env vars, or adds a third toolset
can do it by editing one data file, without touching any of the tool/
endpoint/provider code that reads these values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "toolsets.json"


class ToolsetField(NamedTuple):
    env_var: str
    label: str
    sensitive: bool
    required: bool


def _load() -> dict:
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


_config = _load()

# mcp-agent-skills is internal-only on the compose `default` network (no
# published port); plugin_daemon is on that same network (docker-compose.yml),
# so this is reachable without going through ssrf_proxy.
MCP_SERVER_URL: str = _config["mcp_server_url"]

# How long a credentials-entry link stays valid for. Short-lived on purpose --
# it's meant to be opened right after the tool call that generated it, not
# bookmarked.
LINK_TTL_SECONDS: int = _config["link_ttl_seconds"]

# One entry per agent-skills toolset this plugin knows how to bridge
# credentials for. Each field's env_var is exactly the env var
# skills/<toolset>/lib/auth.py reads and therefore exactly the
# X-Agent-Skills-Env-<VAR> header mcp-server/lib/credentials.py accepts
# (see agent-skills/AUTHENTICATION.md Part 2). `sensitive` only affects how
# the credentials form renders the field (password vs. text input) -- both
# are always encrypted at rest here regardless.
TOOLSET_FIELDS: dict[str, list[ToolsetField]] = {
    toolset: [ToolsetField(**field) for field in fields]
    for toolset, fields in _config["toolsets"].items()
}


def toolset_for_tool_name(tool_name: str) -> str | None:
    """agent-skills' own MCP tool names are always
    ``f"{toolset}_{action}"`` (see agentflow-dify's root AGENTS.md, and
    mcp-server's own tool-naming convention) -- e.g. ``jira_search``,
    ``confluence_create_page``. That means the toolset a call needs
    credentials for can be inferred from the tool name itself, so callers
    of ``mcp_call_tool`` never have to redundantly say "this is a jira
    call" as a second parameter. Returns None for a tool this plugin has
    no stored-credential concept for (e.g. a future non-Jira/Confluence
    toolset, or ``list_skills``/``get_skill``/``doc_gen``) -- those are
    still called, just without any per-user header injected.
    """
    for toolset in TOOLSET_FIELDS:
        if tool_name.startswith(f"{toolset}_"):
            return toolset
    return None
