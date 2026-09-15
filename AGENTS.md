# Agent instructions

## Overview

`agentflow` is a self-hosted, single-front-door agentic chatbot stack:
[Open WebUI](https://openwebui.com/) as the one UI end users see, talking
to a self-hosted, OpenAI-compatible LLM endpoint (vLLM), with Jira and
Confluence exposed as tools via the `agent-skills` MCP server, wired in as
a native Open WebUI Tool (`config/tools/agent_skills.py`). It's an
infrastructure repo -- a pinned `docker-compose.yml` plus config/scripts --
not an application with its own source code to build, aside from that one
Tool file (see "Architecture" below for why it exists at all). Nothing
here names a specific model or organization; every such detail lives in
your own `.env`, not in this doc.

## Setup / bringing the stack up

```bash
cp .env.example .env
scripts/generate-secrets.sh   # paste output into .env, then fill in the rest
chmod 600 .env
make up
```

`make up` (alias `make bootstrap`, same target) is the one idempotent
command for both first run and every re-run: generates
`searxng/settings.yml` from its template with a fresh secret if missing,
builds and starts every service, waits for `open-webui` to report healthy,
creates the admin account if none exists yet, and pushes
`config/tools/agent_skills.py` into Open WebUI as a Tool every user can
use. Safe to rerun. Full variable reference:
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

## Day to day

```bash
make ps                              # health of all services
make logs SERVICE=open-webui         # or any other service name
make build SERVICE=mcp-agent-skills  # rebuild one service's image
make down                            # stop (volumes kept)
make backup                          # snapshot every volume + .env
```

Run `make help` for the full target list -- `Makefile` is a thin wrapper
over `docker compose`/`scripts/`, nothing in it isn't documented in
[`README.md`](README.md) or [`docs/OPERATIONS.md`](docs/OPERATIONS.md) too.

User account management also goes through `make user-*` targets (create,
list, ban, unban, delete, reset-password) -- see README.md "Managing
users" for the full list and what each wraps (they're thin curl+jq
wrappers over Open WebUI's own admin REST API, in
`scripts/openwebui-admin.sh`). Role/permission management (promoting
someone to admin, custom roles, groups) is done in Open WebUI's own
built-in Admin Panel (`/admin`), not the CLI -- see README.md and
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) "Admin panel".

## Testing

There's no application code here to unit-test, aside from
`config/tools/agent_skills.py` itself (plain Python, no test suite of its
own in this repo -- verify a change to it by re-running
`scripts/bootstrap.sh` and exercising the affected tool in a real chat).
The one test command that exists runs `agent-skills`' own `mcp-server`
test suite inside the built image, as a way to verify that submodule pin
is sound in this stack's actual runtime:

```bash
make test
```

## Architecture

`docker-compose.yml` is the architecture -- read it directly for exact
service wiring, networks, and healthchecks; this is a summary, not a
substitute.

- `open-webui` is the only service with a published port and the only
  thing end users talk to. It calls `postgres` (users/chats/Tool
  definitions and Valves, and pgvector-backed RAG for uploaded files),
  `mcp-agent-skills` (via `config/tools/agent_skills.py`'s own outbound
  MCP call, not a native tool-server connection), and `searxng` (native
  web search).
- `mcp-agent-skills` and `searxng` are **internal-only, no published
  port, `backend` network only** -- reachability from `open-webui` is
  their only access control (MCP's HTTP transport has no auth of its
  own). Never add a `ports:` entry to either.
- **Jira/Confluence are a Tool, not a raw MCP connection -- this is the
  one deliberate deviation from "no application source code" in this
  repo.** Open WebUI's native MCP support forwards a caller's *identity*
  headers, never an arbitrary secret a user typed into a form, so it
  can't carry a different Jira/Confluence credential per user by itself
  -- LibreChat's `customUserVars` mechanism (the thing this replaces) had
  no direct Open WebUI equivalent except a Tool's per-user Valves.
  `config/tools/agent_skills.py` is that bridge: it hand-lists
  agent-skills' current `jira_*`/`confluence_*`/`doc_gen`/`get_skill`/
  `list_skills` tool names as typed Python methods (Open WebUI's
  Tool-spec builder needs a real method per callable, confirmed by
  reading `open_webui/utils/tools.py` -- it inspects actual function
  signatures and `:param name: ...`-style docstrings, not a schema
  fetched live per request), each opening its own short-lived MCP session
  to `mcp-agent-skills` and injecting that specific caller's own Valves
  as `X-Agent-Skills-Env-*` headers. See that file's own top-of-file
  comment before changing it, and `docs/OPERATIONS.md` "Updating the
  agent-skills submodule" for what a version bump requires here.
- **A user connecting their own Jira/Confluence is meant to need no
  administrator and no `.env` edit, ever** -- the whole reason the Valves
  form exists. `config/tools/agent_skills.py`'s `_with_setup_hint` turns
  mcp-agent-skills' own `missing_environment_variables` error into a
  `setup_instructions` field naming the exact click path (the **+** menu
  next to the message box -> the sliders icon on this Tool), so a user's
  first attempt at a Jira/Confluence action doubles as onboarding instead
  of a dead end -- see `docs/CONFIGURATION.md` "Per-user Jira/Confluence
  credentials" for the reasoning.
- **Tool approval is two-layered, deliberately**: every write method in
  `config/tools/agent_skills.py` (the 12 in its `WRITE_TOOLS` set) shows
  an Allow/Deny card via `__event_call__` before ever calling
  `mcp-agent-skills`, regardless of Open WebUI's own (per-user,
  switchable) `ENABLE_TOOL_PERMISSIONS` setting; independently, each
  write-capable toolset inside `agent-skills` (Jira and Confluence today)
  refuses to execute without its own `--confirm`, enforced in that repo's
  own code. Neither layer is a substitute for the other -- this replaces
  LibreChat's `toolApproval.ask` list from the old `config/librechat.yaml`,
  which no longer exists in this stack.
- `agent-skills` is a **git submodule pinned to a released tag**, not a
  branch -- so this stack always states exactly which `agent-skills`
  version it's running. Bumping the pin is a reviewable commit; see
  [`docs/OPERATIONS.md`](docs/OPERATIONS.md) "Updating the agent-skills
  submodule" for the exact steps, including when
  `config/tools/agent_skills.py` also needs a matching method added.
  There's no bind-mount for live-editing skills against this stack --
  that's deliberate (reproducibility over iteration speed).
  `agent-skills/AGENTS.md` covers that submodule's own internals; once
  you're working inside `agent-skills/`, that file
  (closest-`AGENTS.md`-wins) governs, not this one.

## Security / credentials

- **Every secret lives in `.env` (gitignored) or `searxng/settings.yml`
  (also gitignored)** -- never in a tracked file. `.env.example` and
  `searxng/settings.yml.example` are the tracked templates, with
  placeholder values only. `scripts/generate-secrets.sh` generates real
  values; never hand-write one into a template file.
- `docker-compose.yml` references secrets only as `${VAR}` env
  interpolation -- if you're about to write a literal secret value into
  it, stop; it belongs in `.env` instead.
- Per-user Jira/Confluence credentials live in Open WebUI's own encrypted
  Tool-Valves storage (each user's own, filled in from inside any chat --
  the **+** button next to the message box -> the sliders icon on "Agent
  Skills (Jira & Confluence)" -- no admin path required, though
  Workspace -> Tools has the same form for whoever prefers it), never in
  `.env` -- `config/tools/agent_skills.py` reads them per-call from
  `__user__["valves"]` and injects them as `X-Agent-Skills-Env-<VAR>`
  headers, trusted by `mcp-agent-skills` only because
  `MCP_TRUST_REQUEST_CREDENTIALS=1` is safe specifically *here* (no
  published port, `open-webui` is the only caller on the `backend`
  network) -- see `agent-skills/AUTHENTICATION.md` before changing
  anything in that trust chain.
- `WEBUI_SECRET_KEY` signs session tokens and encrypts everything Open
  WebUI stores in Postgres, including every user's own Valves. Restoring
  `postgres_data` next to a *different* value makes stored credentials
  permanently undecryptable -- see
  [`docs/OPERATIONS.md`](docs/OPERATIONS.md) "Secrets" before touching
  backup/restore.

## Project map

- [`README.md`](README.md) -- start here: prerequisites, first run, what
  each service is.
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) -- every `.env`
  variable, what breaks if it's wrong, and the per-user credential/tool
  approval model in detail.
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) -- backup/restore, upgrades,
  bumping the `agent-skills` submodule pin, restart ordering.
- [`docs/KEYCLOAK.md`](docs/KEYCLOAK.md) -- optional SSO, and how to
  switch to/from local email/password auth.
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) -- specific known
  failure modes (e.g. the Agent Skills tool missing from the picker).
- `agent-skills/` -- git submodule, pinned to a tag (see Architecture
  above); has its own `AGENTS.md` and `AUTHENTICATION.md` governing that
  subtree.
