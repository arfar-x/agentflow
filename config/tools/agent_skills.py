"""
title: Agent Skills (Jira & Confluence)
author: agentflow
description: >-
  Bridges Open WebUI to this deployment's mcp-agent-skills MCP server,
  injecting each signed-in user's own Jira/Confluence credentials
  (configured below, under this tool's per-user Valves) as
  X-Agent-Skills-Env-* headers on every call -- so Jira's/Confluence's own
  audit log shows the real person, not one shared service account. Write
  actions (anything that changes Jira/Confluence state) always show an
  Allow/Deny card before running.
version: 1.0.0
"""

# agentflow's replacement for LibreChat's `mcpServers.agent-skills` entry in
# config/librechat.yaml (customUserVars + toolApproval.ask). Open WebUI has
# no equivalent of either built in for a raw MCP connection: native MCP
# (Streamable HTTP) forwards only the signed-in user's *identity* headers
# (X-OpenWebUI-User-*), never a per-user secret the user typed into a form,
# and its global "Tool Permissions" toggle is a per-user chat preference,
# not an admin-mandated per-tool-name allowlist. This file is a native
# Open WebUI Tool instead, precisely because Tools are the one place Open
# WebUI *does* support both things natively:
#   - UserValves below == LibreChat's customUserVars, field for field
#     (same 8 vars: JIRA_BASE_URL/USERNAME/PASSWORD/DEFAULT_PROJECT,
#     CONFLUENCE_BASE_URL/USERNAME/PASSWORD/DEFAULT_SPACE). Each user fills
#     these in once, in their own Open WebUI account (Workspace -> Tools ->
#     wrench icon on this tool -> Valves), same one-time-enrollment UX
#     LibreChat's MCP Settings form gave them, stored encrypted at rest by
#     Open WebUI itself the same way it already stores every other Tool's
#     UserValves.
#   - Every write method below (see WRITE_TOOLS) calls _confirm() first,
#     which raises an Allow/Deny card via __event_call__ and refuses to
#     call mcp-agent-skills at all on Deny. This is deliberately
#     independent of Open WebUI's own admin-configurable
#     ENABLE_TOOL_PERMISSIONS toggle (docker-compose.yml sets that too, as
#     defense in depth, but a user can switch it off for themselves --
#     this method-level gate can't be switched off from the chat UI) and
#     of mcp-agent-skills' own --confirm requirement (still enforced
#     server-side, unchanged, in that repo's own code) -- three
#     independent layers now stand between a model and an actual Jira/
#     Confluence write, not fewer than the two LibreChat had.
#
# Every tool name below (jira_*, confluence_*, doc_gen, get_skill,
# list_skills) matches exactly what agent-skills/mcp-server exposes today
# (verified against that repo's skills/jira/SKILL.md, skills/confluence/
# SKILL.md, and mcp-server/server.py) -- but unlike LibreChat's raw MCP
# connection, this list is NOT auto-discovered: mcp-agent-skills builds its
# tool list dynamically from each toolset's argparse definitions
# (mcp-server/lib/introspect.py), and Open WebUI's own Tool-spec builder
# needs real, statically-defined Python methods (confirmed by reading
# open_webui/utils/tools.py -- it parses `inspect.signature` +
# `:param name: ...`-style docstrings off the actual method objects, not
# a live schema fetched per request). Adding a new agent-skills action
# means adding one corresponding method here -- a reviewable, one-method
# commit, in the same spirit as this repo's existing "pin and bump
# deliberately" approach to the agent-skills submodule itself
# (docs/OPERATIONS.md "Updating the agent-skills submodule").

import asyncio
import json
from typing import Optional

from pydantic import BaseModel, Field

# Vendored with Open WebUI itself (open_webui/utils/mcp/client.py uses the
# same package) -- imported here as the public `mcp` PyPI package directly,
# not by reaching into Open WebUI's own internal modules, so this file
# keeps working across an Open WebUI upgrade even if Open WebUI's own
# internal client wrapper is refactored.
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# The 6 Jira and 6 Confluence tool names that mutate Jira/Confluence state
# -- copied verbatim from this deployment's previous config/librechat.yaml
# toolApproval.ask list, which was itself verified against agent-skills'
# skills/jira/SKILL.md rule 5 and skills/confluence/SKILL.md rule 3 (the
# tools those rules say "refuse to execute unless run with --confirm").
WRITE_TOOLS = frozenset(
    {
        "jira_transition",
        "jira_worklog",
        "jira_worklog_edit",
        "jira_worklog_delete",
        "jira_create_issue",
        "jira_edit_issue",
        "confluence_create_page",
        "confluence_update_page",
        "confluence_delete_page",
        "confluence_add_comment",
        "confluence_add_label",
        "confluence_remove_label",
    }
)


def _error(kind: str, message: str) -> str:
    return json.dumps({"error": {"type": kind, "message": message}}, ensure_ascii=False)


class Tools:
    class Valves(BaseModel):
        MCP_URL: str = Field(
            default="http://mcp-agent-skills:8321/mcp",
            description=(
                "mcp-agent-skills' streamable-HTTP endpoint. Leave the default "
                "unless this compose file's service name changes -- it's only "
                "reachable on the backend network either way."
            ),
        )
        MCP_TIMEOUT_SECONDS: float = Field(
            default=100.0,
            description=(
                "Wall-clock budget for one MCP call. mcp-agent-skills caps its "
                "own CLI subprocess at 60s; this stays above that (matching "
                "the old librechat.yaml mcpServers.agent-skills timeout) so a "
                "legitimately slow call isn't aborted client-side first."
            ),
        )

    class UserValves(BaseModel):
        JIRA_BASE_URL: str = Field(default="", description="Jira server URL (e.g. https://jira.mycompany.com).")
        JIRA_USERNAME: str = Field(default="", description="Your own Jira username -- used only for your own Jira tool calls, never stored by agent-skills itself.")
        JIRA_PASSWORD: str = Field(
            default="",
            description="Your own Jira password or PAT -- used only for your own Jira tool calls, never stored by agent-skills itself.",
            json_schema_extra={"type": "password"},
        )
        JIRA_DEFAULT_PROJECT: str = Field(default="", description="Default Jira project key (e.g. PAY) for triage/my_work/sprint/kanban_status when you don't name one explicitly.")
        CONFLUENCE_BASE_URL: str = Field(default="", description="Confluence server URL (e.g. https://mycompany.atlassian.net).")
        CONFLUENCE_USERNAME: str = Field(default="", description="Your own Confluence username -- used only for your own Confluence tool calls, never stored by agent-skills itself.")
        CONFLUENCE_PASSWORD: str = Field(
            default="",
            description="Your own Confluence password or PAT -- used only for your own Confluence tool calls, never stored by agent-skills itself.",
            json_schema_extra={"type": "password"},
        )
        CONFLUENCE_DEFAULT_SPACE: str = Field(default="", description="Default Confluence space key (e.g. ENG) for my_pages/get_page_by_title when you don't name one explicitly.")

    def __init__(self):
        self.valves = self.Valves()

    # ---- MCP transport ---------------------------------------------------

    async def _call(self, tool_name: str, arguments: dict, headers: dict) -> str:
        """Open one streamable-HTTP MCP session, call one tool, close it.
        No session is kept open between calls -- each call is independent,
        the same as a fresh curl/HTTP request would be, and cheap enough
        (a local backend-network call) that pooling isn't worth the extra
        state this single-request-per-call shape avoids.
        """
        arguments = {k: v for k, v in arguments.items() if v is not None}

        async def _run() -> str:
            async with streamablehttp_client(self.valves.MCP_URL, headers=headers) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
            parts = [block.text for block in result.content if getattr(block, "text", None)]
            return "\n".join(parts) if parts else json.dumps({"result": None})

        try:
            return await asyncio.wait_for(_run(), timeout=self.valves.MCP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return _error("timeout", f"{tool_name} did not respond within {self.valves.MCP_TIMEOUT_SECONDS:.0f}s.")
        except Exception as exc:  # noqa: BLE001 -- surfaced to the model as tool output, not raised
            return _error("mcp_call_failed", str(exc))

    @staticmethod
    def _jira_headers(uv: "Tools.UserValves") -> dict:
        pairs = {
            "JIRA_BASE_URL": uv.JIRA_BASE_URL,
            "JIRA_USERNAME": uv.JIRA_USERNAME,
            "JIRA_PASSWORD": uv.JIRA_PASSWORD,
            "JIRA_DEFAULT_PROJECT": uv.JIRA_DEFAULT_PROJECT,
        }
        return {f"X-Agent-Skills-Env-{k}": v for k, v in pairs.items() if v}

    @staticmethod
    def _confluence_headers(uv: "Tools.UserValves") -> dict:
        pairs = {
            "CONFLUENCE_BASE_URL": uv.CONFLUENCE_BASE_URL,
            "CONFLUENCE_USERNAME": uv.CONFLUENCE_USERNAME,
            "CONFLUENCE_PASSWORD": uv.CONFLUENCE_PASSWORD,
            "CONFLUENCE_DEFAULT_SPACE": uv.CONFLUENCE_DEFAULT_SPACE,
        }
        return {f"X-Agent-Skills-Env-{k}": v for k, v in pairs.items() if v}

    @staticmethod
    def _user_valves(__user__: Optional[dict]) -> "Tools.UserValves":
        uv = (__user__ or {}).get("valves")
        return uv if isinstance(uv, Tools.UserValves) else Tools.UserValves()

    # ---- Write-action gate -------------------------------------------------
    # Every WRITE_TOOLS entry goes through here first, unconditionally --
    # this is what replaces librechat.yaml's toolApproval.ask list. Unlike
    # that list, this fires from code that ships with this repo, not a
    # per-user chat preference, so it can't be switched off from the chat
    # UI the way Open WebUI's own Tool Permissions toggle can.

    async def _confirm(self, __event_call__, summary: str) -> bool:
        if __event_call__ is None:
            # No live browser/WebSocket session (e.g. a programmatic API
            # call with no UI attached) -- fail closed, same as the
            # ask_user builtin tool does in this situation.
            return False
        result = await __event_call__(
            {
                "type": "request:user_input",
                "data": {
                    "questions": [
                        {
                            "id": "confirm",
                            "header": "Confirm write action",
                            "question": summary,
                            "options": [
                                {"label": "Confirm", "description": "Yes, do this now."},
                                {"label": "Cancel", "description": "No, don't run this."},
                            ],
                            "allow_other": False,
                        }
                    ],
                    "allow_other": False,
                    "timeout_ms": 120_000,
                },
            }
        )
        if not isinstance(result, dict) or result.get("status") != "answered":
            return False
        answer = (result.get("answers") or {}).get("confirm")
        return isinstance(answer, dict) and answer.get("type") == "option" and answer.get("label") == "Confirm"

    async def _write(
        self,
        tool_name: str,
        arguments: dict,
        headers: dict,
        summary: str,
        __event_call__,
    ) -> str:
        assert tool_name in WRITE_TOOLS
        if not await self._confirm(__event_call__, summary):
            return _error("declined", "The user did not approve this action -- nothing was changed.")
        return await self._call(tool_name, arguments, headers)

    # =======================================================================
    # General (no Jira/Confluence credential needed)
    # =======================================================================

    async def list_skills(self) -> str:
        """List every skill agent-skills exposes (name, kind, one-line
        description). Call this before anything else if you're not sure
        which skill or tool applies -- then get_skill(name) for the full
        instructions body of any skill you need to follow closely (e.g.
        the Jira/Confluence confirmation rules, or a document type's
        exact structure for doc_gen).
        :return: JSON {"skills": [{"name", "kind", "description"}, ...]}
        """
        return await self._call("list_skills", {}, {})

    async def get_skill(self, name: str) -> str:
        """Return one skill's full SKILL.md instructions verbatim. Read
        this for "jira" and "confluence" before their first use in a
        conversation -- it documents required confirmation wording, JQL/
        CQL field-name gotchas, and output formatting rules this tool's
        own short per-action descriptions don't repeat.
        :param name: Skill name, e.g. "jira", "confluence", "prd", "adr".
        :return: JSON {"name", "frontmatter", "instructions"}
        """
        return await self._call("get_skill", {"name": name}, {})

    async def doc_gen(self, doc_type: str) -> str:
        """Fetch the document-generation skill for one doc type (e.g.
        "prd", "trd", "adr", "rfc" -- call list_skills first if unsure
        which are available in this deployment). Returns that skill's
        real instructions for producing the document; it does not write
        the document itself.
        :param doc_type: The document type slug, e.g. "prd".
        :return: JSON skill payload, or an "available" list if doc_type is unknown.
        """
        return await self._call("doc_gen", {"doc_type": doc_type}, {})

    # =======================================================================
    # Jira -- read
    # =======================================================================

    async def jira_my_work(
        self,
        project: Optional[str] = None,
        all_projects: Optional[bool] = None,
        order_by: Optional[str] = None,
        max_results: Optional[int] = None,
        __user__: dict = None,
    ) -> str:
        """Unresolved Jira issues assigned to the current user. Scoped to
        `project` (or your own JIRA_DEFAULT_PROJECT) by default -- pass
        all_projects=true only if the user explicitly asked to broaden.
        :param project: Jira project key, e.g. "PAY". Defaults to your JIRA_DEFAULT_PROJECT.
        :param all_projects: Search every project instead of scoping to one.
        :param order_by: JQL ORDER BY clause, e.g. "updated DESC". Default: "priority DESC, updated DESC".
        :param max_results: Cap on how many issues come back.
        :return: JSON list of matching issues.
        """
        uv = self._user_valves(__user__)
        return await self._call(
            "jira_my_work",
            {"project": project, "all_projects": all_projects, "order_by": order_by, "max_results": max_results},
            self._jira_headers(uv),
        )

    async def jira_issue_summary(self, issue_key: str, sections: Optional[str] = None, __user__: dict = None) -> str:
        """Full context for one Jira issue: fields, comments, worklogs,
        changelog, links.
        :param issue_key: Issue key, e.g. "PAY-123".
        :param sections: Comma-separated subset to fetch, e.g. "issue,worklogs". Default: everything.
        :return: JSON issue detail.
        """
        uv = self._user_valves(__user__)
        return await self._call("jira_issue_summary", {"issue_key": issue_key, "sections": sections}, self._jira_headers(uv))

    async def jira_blockers(self, issue_key: str, __user__: dict = None) -> str:
        """Blocking status and reasons for one Jira issue.
        :param issue_key: Issue key, e.g. "PAY-123".
        :return: JSON {"blocked": bool, "reasons": [...]}
        """
        uv = self._user_valves(__user__)
        return await self._call("jira_blockers", {"issue_key": issue_key}, self._jira_headers(uv))

    async def jira_search(
        self,
        jql: str,
        fields: Optional[str] = None,
        only: Optional[str] = None,
        __user__: dict = None,
    ) -> str:
        """Arbitrary JQL search. `jql` uses Jira's own field names (e.g.
        "due", "issuetype", "reporter") -- these are NOT the same names
        `only` uses (e.g. "due_date", "issue_type"); never mix the two
        vocabularies. "blocked" is always computed and returned.
        :param jql: A Jira Query Language string.
        :param fields: Comma-separated extra Jira field ids to fetch, e.g. "customfield_10056".
        :param only: Comma-separated result field names to return instead of everything, e.g. "summary,status,priority".
        :return: JSON list of matching issues.
        """
        uv = self._user_valves(__user__)
        return await self._call("jira_search", {"jql": jql, "fields": fields, "only": only}, self._jira_headers(uv))

    async def jira_list_fields(self, __user__: dict = None) -> str:
        """Enumerate every Jira field, including custom fields -- use
        this to discover a custom field's real customfield_NNNNN id by
        its label before using it in --custom_fields, --fields, or --jql.
        :return: JSON list of fields.
        """
        uv = self._user_valves(__user__)
        return await self._call("jira_list_fields", {}, self._jira_headers(uv))

    async def jira_now(self, __user__: dict = None) -> str:
        """Current local wall-clock time (no Jira call). Run this before
        resolving any relative date ("now", "yesterday", "last Tuesday")
        for a worklog write -- your own sense of the time is often stale.
        :return: JSON {"now": "<ISO timestamp>"}
        """
        uv = self._user_valves(__user__)
        return await self._call("jira_now", {}, self._jira_headers(uv))

    async def jira_project_context(self, project: Optional[str] = None, __user__: dict = None) -> str:
        """Reference snapshot of a Jira project: issue types, workflow
        statuses, components, priorities, assignable users, and a sample
        of labels in use. Call once per project and remember the result.
        :param project: Jira project key. Defaults to your JIRA_DEFAULT_PROJECT.
        :return: JSON project reference data.
        """
        uv = self._user_valves(__user__)
        return await self._call("jira_project_context", {"project": project}, self._jira_headers(uv))

    async def jira_search_users(
        self,
        query: str,
        project: Optional[str] = None,
        all_projects: Optional[bool] = None,
        __user__: dict = None,
    ) -> str:
        """Look up a Jira user by name/email fragment, to get an
        account_id for a JQL assignee filter or create_issue/edit_issue's
        assignee_account_id. Never invent an account_id from a display
        name.
        :param query: Name or email fragment to search for.
        :param project: Scope to this project's assignable users. Falls back to JIRA_DEFAULT_PROJECT, then instance-wide.
        :param all_projects: Search instance-wide instead of scoping to one project.
        :return: JSON list of matching users.
        """
        uv = self._user_valves(__user__)
        return await self._call(
            "jira_search_users",
            {"query": query, "project": project, "all_projects": all_projects},
            self._jira_headers(uv),
        )

    async def jira_sprint(self, project: Optional[str] = None, board_id: Optional[int] = None, __user__: dict = None) -> str:
        """Active sprint for a Jira board: dates, goal. If the resolved
        board is Kanban (no sprints), this comes back null with a note
        pointing at jira_kanban_status instead -- that's expected, not an error.
        :param project: Jira project key. Defaults to JIRA_DEFAULT_PROJECT.
        :param board_id: Explicit board id, if you already know it.
        :return: JSON sprint detail, or a null sprint with a "note".
        """
        uv = self._user_valves(__user__)
        return await self._call("jira_sprint", {"project": project, "board_id": board_id}, self._jira_headers(uv))

    async def jira_kanban_status(self, project: Optional[str] = None, board_id: Optional[int] = None, __user__: dict = None) -> str:
        """Kanban board columns and per-column issue counts -- the Kanban
        equivalent of jira_sprint for boards with no active sprint.
        :param project: Jira project key. Defaults to JIRA_DEFAULT_PROJECT.
        :param board_id: Explicit board id, if you already know it.
        :return: JSON {"columns": [...], "issue_counts_by_column": {...}}
        """
        uv = self._user_valves(__user__)
        return await self._call("jira_kanban_status", {"project": project, "board_id": board_id}, self._jira_headers(uv))

    async def jira_worklog_report(
        self,
        since: str,
        until: Optional[str] = None,
        max_issues: Optional[int] = None,
        __user__: dict = None,
    ) -> str:
        """Logged time over a date range, vs. each issue's original
        estimate.
        :param since: Start of the range, e.g. "-14d" or an ISO date.
        :param until: End of the range. Defaults to now.
        :param max_issues: Cap on how many issues to include.
        :return: JSON {"total_logged_seconds", "total_delta_seconds", "issues": [...]}
        """
        uv = self._user_valves(__user__)
        return await self._call(
            "jira_worklog_report", {"since": since, "until": until, "max_issues": max_issues}, self._jira_headers(uv)
        )

    async def jira_triage(
        self,
        project: Optional[str] = None,
        parent_issue_types: Optional[str] = None,
        __user__: dict = None,
    ) -> str:
        """Group unresolved Jira stories/bugs/tasks with their labeled
        subtasks, for frontend/backend/design-readiness triage.
        :param project: Jira project key. Defaults to JIRA_DEFAULT_PROJECT.
        :param parent_issue_types: Comma-separated issue types to triage, e.g. "Story,Bug,Task".
        :return: JSON list of parent issues with subtask/needs_triage info.
        """
        uv = self._user_valves(__user__)
        return await self._call(
            "jira_triage", {"project": project, "parent_issue_types": parent_issue_types}, self._jira_headers(uv)
        )

    # =======================================================================
    # Jira -- write (confirmed via _write before ever reaching Jira)
    # =======================================================================

    async def jira_worklog(
        self,
        issue_key: str,
        duration: str,
        description: str,
        date: Optional[str] = None,
        confirm: bool = False,
        __user__: dict = None,
        __event_call__=None,
    ) -> str:
        """Log work against a Jira issue. Resolve a relative day name
        ("yesterday", "last Tuesday") to a real calendar date yourself
        first (call jira_now) -- never guess it. This shows the user an
        Allow/Deny card before logging anything.
        :param issue_key: Issue key, e.g. "PAY-123".
        :param duration: Duration string, e.g. "2h", "4h30m".
        :param description: What the work was.
        :param date: ISO date/datetime, or a relative offset like "-1d". Defaults to now -- always pass this explicitly if the user meant a day other than today.
        :param confirm: Set true once you've already told the user what you're about to log and they said yes.
        :return: JSON worklog result, or a "declined"/"requires_confirmation" error.
        """
        uv = self._user_valves(__user__)
        when = f" dated {date}" if date else ""
        summary = f'Log {duration} on {issue_key}{when}: "{description}"'
        return await self._write(
            "jira_worklog",
            {"issue_key": issue_key, "duration": duration, "description": description, "date": date, "confirm": confirm},
            self._jira_headers(uv),
            summary,
            __event_call__,
        )

    async def jira_transition(self, issue_key: str, status: str, confirm: bool = False, __user__: dict = None, __event_call__=None) -> str:
        """Move a Jira issue to a different status. `status` matches
        case-insensitively with a substring fallback -- pass the user's
        own word through directly. This shows the user an Allow/Deny
        card before changing anything.
        :param issue_key: Issue key, e.g. "PAY-123".
        :param status: Target status/transition name, e.g. "Review", "done".
        :param confirm: Set true once you've already told the user what you're about to do and they said yes.
        :return: JSON result, or a "declined" error. On a bad status name, the error lists every real transition to retry with.
        """
        uv = self._user_valves(__user__)
        summary = f"Move {issue_key} to status: {status}"
        return await self._write(
            "jira_transition", {"issue_key": issue_key, "status": status, "confirm": confirm}, self._jira_headers(uv), summary, __event_call__
        )

    async def jira_worklog_edit(
        self,
        issue_key: str,
        worklog_id: str,
        duration: Optional[str] = None,
        description: Optional[str] = None,
        date: Optional[str] = None,
        confirm: bool = False,
        __user__: dict = None,
        __event_call__=None,
    ) -> str:
        """Edit an existing Jira worklog entry's duration/description/
        date. Find worklog_id via jira_issue_summary's worklogs[].id.
        This shows the user an Allow/Deny card before changing anything.
        :param issue_key: Issue key, e.g. "PAY-123".
        :param worklog_id: The worklog entry's id.
        :param duration: New duration, e.g. "2h", if changing it.
        :param description: New description, if changing it.
        :param date: New ISO date/datetime, if changing it.
        :param confirm: Set true once you've already told the user what you're about to change and they said yes.
        :return: JSON result, or a "declined" error.
        """
        uv = self._user_valves(__user__)
        summary = f"Edit worklog {worklog_id} on {issue_key}"
        return await self._write(
            "jira_worklog_edit",
            {
                "issue_key": issue_key,
                "worklog_id": worklog_id,
                "duration": duration,
                "description": description,
                "date": date,
                "confirm": confirm,
            },
            self._jira_headers(uv),
            summary,
            __event_call__,
        )

    async def jira_worklog_delete(self, issue_key: str, worklog_id: str, confirm: bool = False, __user__: dict = None, __event_call__=None) -> str:
        """Permanently delete a Jira worklog entry. Irreversible --
        confirm exactly which entry (issue, duration, date if known)
        with the user in chat before calling this. This shows the user a
        second Allow/Deny card before deleting anything.
        :param issue_key: Issue key, e.g. "PAY-123".
        :param worklog_id: The worklog entry's id.
        :param confirm: Set true once you've already confirmed the specific entry with the user and they said yes.
        :return: JSON result, or a "declined" error.
        """
        uv = self._user_valves(__user__)
        summary = f"Permanently delete worklog {worklog_id} on {issue_key}"
        return await self._write(
            "jira_worklog_delete", {"issue_key": issue_key, "worklog_id": worklog_id, "confirm": confirm}, self._jira_headers(uv), summary, __event_call__
        )

    async def jira_create_issue(
        self,
        project: str,
        summary: str,
        issue_type: str,
        description: Optional[str] = None,
        parent_key: Optional[str] = None,
        labels: Optional[str] = None,
        assignee_account_id: Optional[str] = None,
        priority: Optional[str] = None,
        components: Optional[str] = None,
        custom_fields: Optional[str] = None,
        confirm: bool = False,
        __user__: dict = None,
        __event_call__=None,
    ) -> str:
        """Create a Jira issue or subtask (pass issue_type="Sub-task" and
        parent_key for a subtask). Never invent assignee_account_id from
        a display name -- resolve it via jira_search_users first. This
        shows the user an Allow/Deny card before creating anything.
        :param project: Jira project key, e.g. "PAY".
        :param summary: Issue summary/title.
        :param issue_type: e.g. "Bug", "Task", "Story", "Sub-task".
        :param description: Issue description.
        :param parent_key: Parent issue key, required for a subtask.
        :param labels: Comma-separated labels, e.g. "Frontend,UX".
        :param assignee_account_id: Assignee's account id, from jira_search_users -- never a guessed name.
        :param priority: e.g. "High".
        :param components: Comma-separated component names.
        :param custom_fields: JSON object string of customfield_NNNNN -> value, ids resolved via jira_list_fields.
        :param confirm: Set true once you've already told the user what you're about to create and they said yes.
        :return: JSON created-issue result, or a "declined" error.
        """
        uv = self._user_valves(__user__)
        summary_line = f'Create {issue_type} in {project}: "{summary}"' + (f" under {parent_key}" if parent_key else "")
        return await self._write(
            "jira_create_issue",
            {
                "project": project,
                "summary": summary,
                "issue_type": issue_type,
                "description": description,
                "parent_key": parent_key,
                "labels": labels,
                "assignee_account_id": assignee_account_id,
                "priority": priority,
                "components": components,
                "custom_fields": custom_fields,
                "confirm": confirm,
            },
            self._jira_headers(uv),
            summary_line,
            __event_call__,
        )

    async def jira_edit_issue(
        self,
        issue_key: str,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        labels: Optional[str] = None,
        assignee_account_id: Optional[str] = None,
        priority: Optional[str] = None,
        components: Optional[str] = None,
        custom_fields: Optional[str] = None,
        confirm: bool = False,
        __user__: dict = None,
        __event_call__=None,
    ) -> str:
        """Update fields on an existing Jira issue or subtask. Never
        invent assignee_account_id from a display name -- resolve it via
        jira_search_users first. This shows the user an Allow/Deny card
        before changing anything.
        :param issue_key: Issue key, e.g. "PAY-123".
        :param summary: New summary/title, if changing it.
        :param description: New description, if changing it.
        :param labels: Comma-separated labels to set.
        :param assignee_account_id: New assignee's account id, from jira_search_users.
        :param priority: New priority, e.g. "High".
        :param components: Comma-separated component names to set.
        :param custom_fields: JSON object string of customfield_NNNNN -> value.
        :param confirm: Set true once you've already told the user what you're about to change and they said yes.
        :return: JSON result, or a "declined" error.
        """
        uv = self._user_valves(__user__)
        summary_line = f"Edit {issue_key}"
        return await self._write(
            "jira_edit_issue",
            {
                "issue_key": issue_key,
                "summary": summary,
                "description": description,
                "labels": labels,
                "assignee_account_id": assignee_account_id,
                "priority": priority,
                "components": components,
                "custom_fields": custom_fields,
                "confirm": confirm,
            },
            self._jira_headers(uv),
            summary_line,
            __event_call__,
        )

    # =======================================================================
    # Confluence -- read
    # =======================================================================

    async def confluence_get_page(self, page_id: str, __user__: dict = None) -> str:
        """Fetch one Confluence page by its content id: body, version,
        space, ancestors, history.
        :param page_id: Confluence page id, e.g. "12345678".
        :return: JSON page detail.
        """
        uv = self._user_valves(__user__)
        return await self._call("confluence_get_page", {"page_id": page_id}, self._confluence_headers(uv))

    async def confluence_get_page_by_title(self, space_key: str, title: str, __user__: dict = None) -> str:
        """Resolve a Confluence page by its space + exact title -- the
        common case when a user names a page by what it's called.
        :param space_key: Confluence space key, e.g. "ENG".
        :param title: Exact page title.
        :return: JSON page detail.
        """
        uv = self._user_valves(__user__)
        return await self._call("confluence_get_page_by_title", {"space_key": space_key, "title": title}, self._confluence_headers(uv))

    async def confluence_search(
        self, cql: str, max_results: Optional[int] = None, include_body: Optional[bool] = None, __user__: dict = None
    ) -> str:
        """Arbitrary CQL search. include_body opts into fetching each
        result's body text (off by default -- bodies are the largest
        single field and bulk searches rarely need full text for every
        match).
        :param cql: A Confluence Query Language string.
        :param max_results: Cap on how many results come back. Default 25.
        :param include_body: Fetch each result's body text too.
        :return: JSON list of matching pages.
        """
        uv = self._user_valves(__user__)
        return await self._call(
            "confluence_search", {"cql": cql, "max_results": max_results, "include_body": include_body}, self._confluence_headers(uv)
        )

    async def confluence_list_spaces(self, __user__: dict = None) -> str:
        """Enumerate every Confluence space visible to the authenticated user.
        :return: JSON list of spaces.
        """
        uv = self._user_valves(__user__)
        return await self._call("confluence_list_spaces", {}, self._confluence_headers(uv))

    async def confluence_get_space(self, space_key: str, __user__: dict = None) -> str:
        """Fetch one Confluence space's identity and description.
        :param space_key: Confluence space key, e.g. "ENG".
        :return: JSON space detail.
        """
        uv = self._user_valves(__user__)
        return await self._call("confluence_get_space", {"space_key": space_key}, self._confluence_headers(uv))

    async def confluence_get_comments(self, page_id: str, __user__: dict = None) -> str:
        """Every comment on a Confluence page.
        :param page_id: Confluence page id.
        :return: JSON list of comments.
        """
        uv = self._user_valves(__user__)
        return await self._call("confluence_get_comments", {"page_id": page_id}, self._confluence_headers(uv))

    async def confluence_get_attachments(self, page_id: str, __user__: dict = None) -> str:
        """Every attachment (metadata only) on a Confluence page.
        :param page_id: Confluence page id.
        :return: JSON list of attachments.
        """
        uv = self._user_valves(__user__)
        return await self._call("confluence_get_attachments", {"page_id": page_id}, self._confluence_headers(uv))

    async def confluence_get_children(self, page_id: str, __user__: dict = None) -> str:
        """Every direct child page of a Confluence page.
        :param page_id: Confluence page id.
        :return: JSON list of child pages.
        """
        uv = self._user_valves(__user__)
        return await self._call("confluence_get_children", {"page_id": page_id}, self._confluence_headers(uv))

    async def confluence_get_labels(self, page_id: str, __user__: dict = None) -> str:
        """Every label on a Confluence page.
        :param page_id: Confluence page id.
        :return: JSON list of labels.
        """
        uv = self._user_valves(__user__)
        return await self._call("confluence_get_labels", {"page_id": page_id}, self._confluence_headers(uv))

    async def confluence_page_summary(self, page_id: str, sections: Optional[str] = None, __user__: dict = None) -> str:
        """Full context for one Confluence page in a single call:
        content, comments, attachments, labels, children.
        :param page_id: Confluence page id.
        :param sections: Comma-separated subset to fetch, e.g. "page,comments". Default: everything.
        :return: JSON page detail.
        """
        uv = self._user_valves(__user__)
        return await self._call("confluence_page_summary", {"page_id": page_id, "sections": sections}, self._confluence_headers(uv))

    async def confluence_my_pages(self, max_results: Optional[int] = None, __user__: dict = None) -> str:
        """Confluence pages the current user authored, most recently
        modified first.
        :param max_results: Cap on how many pages come back. Default 25.
        :return: JSON list of pages.
        """
        uv = self._user_valves(__user__)
        return await self._call("confluence_my_pages", {"max_results": max_results}, self._confluence_headers(uv))

    # =======================================================================
    # Confluence -- write (confirmed via _write before ever reaching Confluence)
    # =======================================================================

    async def confluence_create_page(
        self,
        space_key: str,
        title: str,
        body_storage: str,
        parent_id: Optional[str] = None,
        confirm: bool = False,
        __user__: dict = None,
        __event_call__=None,
    ) -> str:
        """Create a Confluence page. body_storage MUST be Confluence
        storage-format XHTML (e.g. "<p>...</p>", "<h2>...</h2>"), never
        Markdown -- Markdown syntax is not converted and renders as
        literal garbage text. This shows the user an Allow/Deny card
        before creating anything.
        :param space_key: Confluence space key, e.g. "ENG".
        :param title: Page title.
        :param body_storage: Page content as Confluence storage-format XHTML.
        :param parent_id: Parent page id, to create this as a child page.
        :param confirm: Set true once you've already told the user the title/space/content and they said yes.
        :return: JSON created-page result, or a "declined" error.
        """
        uv = self._user_valves(__user__)
        summary = f'Create Confluence page "{title}" in {space_key}'
        return await self._write(
            "confluence_create_page",
            {"space_key": space_key, "title": title, "body_storage": body_storage, "parent_id": parent_id, "confirm": confirm},
            self._confluence_headers(uv),
            summary,
            __event_call__,
        )

    async def confluence_update_page(
        self,
        page_id: str,
        title: Optional[str] = None,
        body_storage: Optional[str] = None,
        confirm: bool = False,
        __user__: dict = None,
        __event_call__=None,
    ) -> str:
        """Update a Confluence page's title and/or content. body_storage
        replaces the ENTIRE page body -- there is no append; fetch the
        current content with confluence_get_page first and compose the
        full new body. Version is resolved/incremented automatically.
        This shows the user an Allow/Deny card before changing anything.
        :param page_id: Confluence page id.
        :param title: New title, if changing it.
        :param body_storage: Full new page content as Confluence storage-format XHTML, if changing it.
        :param confirm: Set true once you've already shown the user the full new content and they said yes.
        :return: JSON result, or a "declined" error. A version-conflict error means the page changed since you last read it -- re-fetch and re-diff rather than retrying blindly.
        """
        uv = self._user_valves(__user__)
        summary = f"Update Confluence page {page_id}"
        return await self._write(
            "confluence_update_page",
            {"page_id": page_id, "title": title, "body_storage": body_storage, "confirm": confirm},
            self._confluence_headers(uv),
            summary,
            __event_call__,
        )

    async def confluence_delete_page(self, page_id: str, confirm: bool = False, __user__: dict = None, __event_call__=None) -> str:
        """Permanently delete a Confluence page. Irreversible -- confirm
        exactly which page (title, space, id) with the user in chat
        before calling this. This shows the user a second Allow/Deny
        card before deleting anything.
        :param page_id: Confluence page id.
        :param confirm: Set true once you've already confirmed the specific page with the user and they said yes.
        :return: JSON result, or a "declined" error.
        """
        uv = self._user_valves(__user__)
        summary = f"Permanently delete Confluence page {page_id}"
        return await self._write(
            "confluence_delete_page", {"page_id": page_id, "confirm": confirm}, self._confluence_headers(uv), summary, __event_call__
        )

    async def confluence_add_comment(self, page_id: str, body_storage: str, confirm: bool = False, __user__: dict = None, __event_call__=None) -> str:
        """Add a comment to a Confluence page. body_storage MUST be
        Confluence storage-format XHTML, never Markdown. This shows the
        user an Allow/Deny card before posting anything.
        :param page_id: Confluence page id.
        :param body_storage: Comment content as Confluence storage-format XHTML.
        :param confirm: Set true once you've already shown the user the comment text and they said yes.
        :return: JSON result, or a "declined" error.
        """
        uv = self._user_valves(__user__)
        summary = f"Add a comment to Confluence page {page_id}"
        return await self._write(
            "confluence_add_comment", {"page_id": page_id, "body_storage": body_storage, "confirm": confirm}, self._confluence_headers(uv), summary, __event_call__
        )

    async def confluence_add_label(self, page_id: str, label: str, confirm: bool = False, __user__: dict = None, __event_call__=None) -> str:
        """Add a label to a Confluence page. This shows the user an
        Allow/Deny card before changing anything.
        :param page_id: Confluence page id.
        :param label: Label to add.
        :param confirm: Set true once you've already told the user which label and they said yes.
        :return: JSON result, or a "declined" error.
        """
        uv = self._user_valves(__user__)
        summary = f'Add label "{label}" to Confluence page {page_id}'
        return await self._write(
            "confluence_add_label", {"page_id": page_id, "label": label, "confirm": confirm}, self._confluence_headers(uv), summary, __event_call__
        )

    async def confluence_remove_label(self, page_id: str, label: str, confirm: bool = False, __user__: dict = None, __event_call__=None) -> str:
        """Remove a label from a Confluence page. This shows the user an
        Allow/Deny card before changing anything.
        :param page_id: Confluence page id.
        :param label: Label to remove.
        :param confirm: Set true once you've already told the user which label and they said yes.
        :return: JSON result, or a "declined" error.
        """
        uv = self._user_valves(__user__)
        summary = f'Remove label "{label}" from Confluence page {page_id}'
        return await self._write(
            "confluence_remove_label", {"page_id": page_id, "label": label, "confirm": confirm}, self._confluence_headers(uv), summary, __event_call__
        )
