# Configuration reference

Every variable `.env.example` declares, what it's for, and what breaks if
it's wrong or missing. Copy `.env.example` to `.env`, fill these in, then
`chmod 600 .env`.

## Open WebUI / port

| Variable | Required | Default | Notes |
|---|---|---|---|
| `PORT` | no | `3080` | Port `open-webui` listens on, bound to `127.0.0.1` only -- put a reverse proxy in front for TLS. |
| `OPEN_WEBUI_IMAGE_TAG` | yes | none | Pin a real published tag before production. `main` moves under you without warning. |

## Secrets

Generate with `scripts/generate-secrets.sh` -- don't hand-write these.

| Variable | Required | Notes |
|---|---|---|
| `WEBUI_SECRET_KEY` | yes | Signs session tokens and encrypts every credential Open WebUI stores at rest -- including each user's own Jira/Confluence Valves in `config/tools/agent_skills.py`. Open WebUI's single equivalent of LibreChat's four secrets (`CREDS_KEY`/`CREDS_IV`/`JWT_SECRET`/`JWT_REFRESH_SECRET`) combined. |

**Rotating this on a live deployment makes existing stored credentials
permanently unreadable.** They don't get re-encrypted -- they just stop
decrypting. See `docs/OPERATIONS.md`.

## Postgres

Serves two roles in this stack with one database: Open WebUI's own app
data (users, chats, Tool definitions and Valves), and its pgvector-backed
RAG store for per-conversation file uploads.

| Variable | Required | Notes |
|---|---|---|
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | yes | Credentials for the `postgres` container itself; `open-webui`'s `DATABASE_URL` and `PGVECTOR_DB_URL` are both built from these directly in `docker-compose.yml`. |

## LLM endpoint (vLLM)

| Variable | Required | Notes |
|---|---|---|
| `VLLM_BASE_URL` | yes | OpenAI-compatible base URL, e.g. `https://vllm.internal/v1`. Passed straight through as Open WebUI's `OPENAI_API_BASE_URLS`. |
| `VLLM_API_KEY` | yes (or blank if your endpoint has none) | Passed as a Bearer token (`OPENAI_API_KEYS`). |

**The advertised-model-id trap:** some vLLM setups advertise a model `id`
that doesn't match the model actually being served (this deployment's
endpoint, for example, advertises `id: "claude-sonnet-4-6"` for a
different underlying model). Open WebUI shows that raw id in the model
picker by default. Give it a real display name once, in **Admin Settings
-> Models -> (the model) -> pencil icon**: set **Name** to whatever label
you want users to see, and (optional) a **Description**. This is a
one-time UI step, not something `scripts/bootstrap.sh` automates --
unlike LibreChat's `tokenConfig`, Open WebUI doesn't need an explicit
context-length override to behave correctly against an unfamiliar model
id, so there's nothing unsafe about leaving it un-renamed either; it's
purely cosmetic.

## MCP: agent-skills

| Variable | Required | Notes |
|---|---|---|
| `MCP_TOOLSETS` | no | Default `jira`. **Quote it** if it lists more than one (`MCP_TOOLSETS="jira confluence"`) -- an unquoted space-separated value breaks any script that `source`s `.env`. Space-separated toolset names, each needing `skills/<name>/requirements.txt` to exist in the `agent-skills` submodule. Changing this requires `docker compose up -d --build mcp-agent-skills`. |
| `JIRA_BASE_URL` / `JIRA_USERNAME` / `JIRA_PASSWORD` | no (server-level) | Left blank in this deployment by design -- every Jira tool call's base URL and credential comes per-user instead, via `config/tools/agent_skills.py`'s own Valves (see below). Set these only if you want a shared fallback identity instead of per-user. |
| `JIRA_AUTO_CONFIRM_WRITES` | no | Leave `false` in production. `config/tools/agent_skills.py`'s own confirmation card is the intended approval surface; this variable existing at all is an escape hatch for automation you explicitly trust, not something to flip for convenience. |
| `JIRA_DEFAULT_PROJECT` | no | Fallback for `jira_triage`/`my_work`/`sprint`/`kanban_status` when a user hasn't set their own `JIRA_DEFAULT_PROJECT` Valve. |
| `JIRA_DEPLOYMENT_TYPE` | no | Only required the first time `create_issue`/`edit_issue` sets an assignee. |
| `CONFLUENCE_BASE_URL` / `CONFLUENCE_USERNAME` / `CONFLUENCE_PASSWORD` | no (server-level) | Same per-user pattern as Jira above -- left blank here, supplied per-user via the Tool's Valves. |
| `CONFLUENCE_AUTO_CONFIRM_WRITES` | no | Same reasoning as `JIRA_AUTO_CONFIRM_WRITES` -- leave `false`. |
| `CONFLUENCE_DEFAULT_SPACE` | no | Fallback for `my_pages`/`get_page_by_title` when a user hasn't set their own `CONFLUENCE_DEFAULT_SPACE` Valve. |
| `CONFLUENCE_DEPLOYMENT_TYPE` | yes, if `confluence` is in `MCP_TOOLSETS` | `cloud` or `server` -- unlike Jira, this one is required: Confluence's REST API is mounted at a different path per deployment (Cloud: `/wiki/rest/api`, Server/DC: `/rest/api`). Server-level, not per-user -- the whole org's Confluence instance is one deployment type. |

### Per-user Jira/Confluence credentials

LibreChat had a purpose-built mechanism for this (`customUserVars`): each
user fills in their own credential once, in a settings form, and it gets
injected as a header on every one of their own MCP calls. Open WebUI's
native MCP support has no equivalent -- it forwards a caller's *identity*
(their Open WebUI user id/email), never an arbitrary secret they typed
into a form, so a raw MCP connection to `mcp-agent-skills` would leave
every user hitting Jira as whatever credential the *server itself* has
configured (or none).

`config/tools/agent_skills.py` closes that gap using the one part of Open
WebUI that *does* support this natively: a **Tool**'s per-user **Valves**.
Each user fills in `JIRA_BASE_URL`/`JIRA_USERNAME`/`JIRA_PASSWORD`/
`JIRA_DEFAULT_PROJECT`/`CONFLUENCE_BASE_URL`/`CONFLUENCE_USERNAME`/
`CONFLUENCE_PASSWORD`/`CONFLUENCE_DEFAULT_SPACE` once, in **Workspace ->
Tools -> the wrench icon on "Agent Skills (Jira & Confluence)" -> Valves**
-- Open WebUI stores it encrypted at rest (keyed from `WEBUI_SECRET_KEY`)
and never shows it to any other user. On every Jira/Confluence tool call,
the plugin builds the exact same `X-Agent-Skills-Env-<VAR>` headers
LibreChat used to send, from that specific caller's own Valves, so
Jira/Confluence's own audit log shows the real person -- see
`agent-skills/AUTHENTICATION.md` Part 2 for how `mcp-agent-skills` honors
those headers, unchanged from before.

### Tool approval

Two independent layers stand between a model and an actual Jira/Confluence
write, same as before, just relocated:

1. **`config/tools/agent_skills.py`'s own confirmation card.** Every
   `jira_transition`/`jira_worklog`/`jira_worklog_edit`/
   `jira_worklog_delete`/`jira_create_issue`/`jira_edit_issue`/
   `confluence_create_page`/`confluence_update_page`/
   `confluence_delete_page`/`confluence_add_comment`/`confluence_add_label`/
   `confluence_remove_label` call shows an Allow/Deny card (naming the
   exact action) before it ever reaches `mcp-agent-skills` -- this is
   code in the Tool itself, not a setting a user can switch off. This
   replaces LibreChat's `toolApproval.ask` list.
2. **`mcp-agent-skills`'s own `--confirm` requirement**, enforced in
   `agent-skills`' own code, unchanged from before: a write tool refuses
   to execute unless its arguments include `confirm: true`, which the
   *model* only sets after it has separately told the user what it's
   about to do (per that skill's own `SKILL.md` rules) and gotten a yes.

`docker-compose.yml` also sets `ENABLE_TOOL_PERMISSIONS=true`, which lets
a user additionally switch their own chats to "Ask for approval" for
*every* tool call, from the chat input's `+` menu -- this is a genuinely
useful per-user preference, but it is **not** the authoritative gate:
it's a chat-level setting a user can switch back off for themselves,
unlike layer 1 above. Don't rely on it alone for anything that needs to
be enforced regardless of a user's own settings.

## Auth

Two mutually exclusive modes -- see `docs/KEYCLOAK.md` for the full Keycloak
walkthrough and how to switch between them.

| Variable | Notes |
|---|---|
| `ENABLE_SIGNUP` | Keep `false` unless you specifically want open self-registration. New accounts normally come from `make user-create` instead -- see README.md "Managing users". Open WebUI never gates the very first account (the one `make up`/`scripts/bootstrap.sh` creates) behind this, so it stays `false` even on a brand-new deployment. |
| `WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_NAME` / `WEBUI_ADMIN_PASSWORD` | The one account `make up`/`scripts/bootstrap.sh` creates automatically on a fresh deployment (skipped once any user exists). Auto-promoted to Open WebUI's admin role because it's the first account ever created in this deployment. Open WebUI identifies accounts by email; there's no separate username field the way LibreChat had. Promoting anyone else to admin afterwards is done from the Admin Panel (`/admin`), not by editing `.env`. |
| `OAUTH_*` / `OPENID_PROVIDER_URL` (commented out by default) | The whole Keycloak OIDC block. Uncommenting it and setting `ENABLE_OAUTH_SIGNUP=true` switches auth modes on the next `open-webui` restart -- no rebuild. |

## Admin panel

Unlike LibreChat, there's no separate `admin-panel` service -- Open WebUI
ships its own, at `http://localhost:${PORT:-3080}/admin` (log in with an
existing admin account). It manages *existing* accounts (promote a role,
build groups, configure model access); creating an account still goes
through `make user-create` -- see README.md "Managing users" for that.
