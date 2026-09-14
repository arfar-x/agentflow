# Troubleshooting

## The "Agent Skills (Jira & Confluence)" tool doesn't show up in the tool picker

1. Confirm it actually loaded: **Workspace -> Tools**. If it's missing
   entirely, `scripts/bootstrap.sh` likely failed to sign in as admin --
   rerun it and read its output; it prints exactly which step failed and
   the manual fallback (**Admin Panel -> Workspace -> Tools -> Import**,
   uploading `config/tools/agent_skills.py` by hand).
2. If it's listed but a chat can't see it, its sharing may still be
   admin-only. Open it (**Workspace -> Tools -> Agent Skills...**) and
   confirm **Visibility** is Public, not Private -- `scripts/bootstrap.sh`
   sets this automatically, but a change made by hand later can revert it.
3. If it's listed with a red error banner instead of the Valves form, the
   Tool failed to import (a Python exception on load) -- click into it to
   see the traceback. The most likely cause after an Open WebUI upgrade is
   `from mcp.client.streamable_http import streamablehttp_client` no
   longer resolving because that internal name changed between `mcp`
   package versions bundled in different Open WebUI releases -- check
   `backend/requirements.txt` in the `open-webui/open-webui` repo for the
   pinned `mcp` version and confirm this import still matches its public API.

## A Jira/Confluence tool call returns `{"error": {"type": "missing_environment_variables", ...}}`

The calling user hasn't filled in their own credentials yet, and no
server-level fallback (`JIRA_USERNAME`/`JIRA_PASSWORD` etc. in `.env`) is
set either. Point them at **Workspace -> Tools -> the wrench icon on
"Agent Skills (Jira & Confluence)" -> Valves** to enter their own
`JIRA_BASE_URL`/`JIRA_USERNAME`/`JIRA_PASSWORD` (or the `CONFLUENCE_*`
equivalents).

## Every Jira/Confluence call hits the same account regardless of who's chatting

Confirm `MCP_TRUST_REQUEST_CREDENTIALS=1` is actually set on
`mcp-agent-skills` (`docker compose exec mcp-agent-skills env | grep
MCP_TRUST_REQUEST_CREDENTIALS`) -- without it, `mcp-agent-skills` ignores
the `X-Agent-Skills-Env-*` headers entirely and falls back to its own
process environment, by design (see
`agent-skills/AUTHENTICATION.md` Part 2). If that's set and this is still
happening, check that the affected users have actually filled in their
own Valves (see previous entry) -- an empty Valve field sends no header
for that variable at all, which falls back the same way.

## A write action doesn't show a confirmation card

`config/tools/agent_skills.py`'s `_confirm()` helper fails closed (denies
the action) if there's no active WebSocket/browser session to show a card
in -- this is intentional, not a bug, and matches how the builtin
`ask_user` tool behaves in the same situation. If a normal, connected chat
session isn't showing the card at all, check the browser console for a
WebSocket error, and confirm `ENABLE_WEBSOCKET_SUPPORT`/reverse-proxy
WebSocket passthrough is configured correctly in front of `open-webui`
(a plain HTTP-only proxy in front of it breaks this).

## A tool call hangs, then times out

`mcp-server/lib/execute.py` caps each CLI subprocess at 60 seconds.
`config/tools/agent_skills.py`'s `Valves.MCP_TIMEOUT_SECONDS` defaults to
100s specifically so it never fires first. If you're still seeing
timeouts:

- Check `docker compose logs mcp-agent-skills` for what the subprocess was
  doing (e.g. Jira itself being slow).
- Confirm nothing downstream (a corporate proxy, Jira/Confluence's own
  network) is the actual slow leg -- the 60s cap is per CLI subprocess call,
  not per conversation turn.

## `mcp-agent-skills` container exits immediately on startup

Almost always `AGENT_SKILLS_REPO_ROOT` pointing at a directory with no
`skills/` child -- `mcp-server/lib/registry.py` has no existence guard and
crashes at import, before the server ever starts listening. This shouldn't
happen with this stack's Dockerfile (it sets `AGENT_SKILLS_REPO_ROOT=/opt/repo`
and copies `skills/` there itself), but would show up this way if the
Dockerfile is ever edited to change that layout.

## Cloned the repo and the `agent-skills/` directory is empty

You forgot `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

`docker compose build mcp-agent-skills` will otherwise fail with a missing
`mcp-server/requirements.txt` or similar, since the build context
(`./agent-skills`) is empty.

## Confirm `mcp-agent-skills`'s tool list directly, bypassing Open WebUI

```bash
docker compose exec open-webui sh -c "curl -s http://mcp-agent-skills:8321/mcp -X POST \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2026-03-26\",\"capabilities\":{},\"clientInfo\":{\"name\":\"debug\",\"version\":\"0\"}}}'"
```

A `200` with a JSON-RPC result means `mcp-agent-skills` itself is fine and
the problem is in `config/tools/agent_skills.py`'s connection to it, not
in `agent-skills`.

## `open-webui` still fails Postgres auth after changing `POSTGRES_PASSWORD`

The official `postgres`/`pgvector` image only applies
`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` the first time
`postgres_data` is initialized -- editing `.env` on an already-initialized
volume changes nothing inside Postgres; the old password is still what's
actually stored. Either update it inside Postgres directly (`docker
compose exec postgres psql -U ${POSTGRES_USER} -c "ALTER USER
${POSTGRES_USER} WITH PASSWORD '...';"`) or, on a deployment with nothing
worth keeping yet, wipe and reinit:

```bash
docker compose down
docker volume rm agentflow_postgres_data
scripts/bootstrap.sh
```

## `scripts/bootstrap.sh` times out waiting for `open-webui` to be healthy

Check `docker compose logs open-webui` directly -- the script only polls
`docker compose ps`'s health status, so whatever's actually failing
(Postgres auth, a bad `VLLM_BASE_URL`, `WEBUI_SECRET_KEY` unset) will be in
those logs, not in the script's own output. The healthcheck's
`start_period` is 60s and the script polls for up to 5 minutes, so a
slow-but-working boot won't false-positive here -- a timeout means
something is genuinely stuck.

## `scripts/bootstrap.sh` reports it couldn't sign in to push the Tool

Most likely `WEBUI_ADMIN_PASSWORD` in `.env` no longer matches a real admin
account (e.g. someone reset it by hand through the UI). Sign in as an
admin manually, generate a new password
(`make user-reset-password EMAIL=<that admin's email>`), update
`WEBUI_ADMIN_PASSWORD` in `.env` to match, and rerun -- or just import
`config/tools/agent_skills.py` by hand once
(**Admin Panel -> Workspace -> Tools -> Import**) and don't worry about
the script re-syncing it automatically going forward.

## OIDC login redirects to Keycloak but comes back with an error

- **redirect_uri mismatch**: the URI Keycloak sees on the way back must
  exactly match a **Valid redirect URI** on the client, including scheme
  and trailing path -- `https://<host>/oauth/oidc/login/callback`, not a
  trailing-slash or `http://` variant.
- **Access refused after a successful login**: the user's token doesn't
  carry the role named in `OAUTH_ALLOWED_ROLES` under the claim named in
  `OAUTH_ROLES_CLAIM` -- see `docs/KEYCLOAK.md` step 4 for how to check
  this with jwt.io, and step 3 for the client-scope mapper Keycloak needs
  to put realm roles at a top-level claim at all.
- **`OPENID_PROVIDER_URL` mismatch**: must be the full
  `.../.well-known/openid-configuration` discovery URL, not just the bare
  realm root -- Open WebUI fetches every other OIDC endpoint from that
  document directly.

## RTL / Persian text rendering oddly

Expected in some cases and worth checking against Open WebUI's current
release before assuming it's this stack's problem. Open WebUI is
MIT-licensed, so a real rendering bug (bidi isolation on inline
identifiers, code blocks inheriting the paragraph's direction) is a
patchable frontend fix, not a blocker to route around.
