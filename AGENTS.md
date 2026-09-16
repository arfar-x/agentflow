# Agent instructions

## Overview

`agentflow` is a self-hosted, single-front-door agentic chatbot stack:
[Dify](https://dify.ai/) Community Edition, pinned to `1.17.1`, as the
platform end users build and use chat apps in, talking to a self-hosted,
OpenAI-compatible LLM endpoint (vLLM), with Jira and Confluence exposed as
tools via the `agent-skills` MCP server, registered with Dify
declaratively (a real API call `scripts/bootstrap.sh` makes, not a form
filled in by hand). It's an infrastructure repo -- Dify's own official
`docker-compose.yaml`, hand-trimmed, plus config/scripts -- not an
application with its own source code to build. Nothing here names a
specific model or organization; every such detail lives in your own
`.env`, not in this doc.

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
builds and starts every service, waits for `api` to report healthy,
creates the admin account via Dify's own `/console/api/setup` if none
exists yet, then registers `mcp-agent-skills` as a Dify MCP tool provider
and (if the model plugin is installed) the vLLM endpoint as a model, both
via Dify's console API. Safe to rerun. Full variable reference:
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

## Day to day

```bash
make ps                              # health of all services
make logs SERVICE=api                # or any other service name
make build SERVICE=mcp-agent-skills  # rebuild one service's image
make down                            # stop (volumes/ bind mounts kept)
make backup                          # snapshot volumes/ + .env
```

Run `make help` for the full target list -- `Makefile` is a thin wrapper
over `docker compose`/`scripts/`, nothing in it isn't documented in
[`README.md`](README.md) or [`docs/OPERATIONS.md`](docs/OPERATIONS.md) too.

Workspace member management has no CLI equivalent here -- it's a Studio
UI action (Settings -> Members -> Invite Members). See README.md
"Managing workspace members".

## Testing

There's no application code here to unit-test. The one test command that
exists runs `agent-skills`' own `mcp-server` test suite inside the built
image, as a way to verify that submodule pin is sound in this stack's
actual runtime:

```bash
make test
```

## Architecture

`docker-compose.yml` is the architecture -- read it directly for exact
service wiring, networks, and healthchecks; this is a summary, not a
substitute. It is Dify's own official `docker/docker-compose.yaml` (tag
`1.17.1`), hand-trimmed to the `pgvector` vector-store profile (every
other vector-store backend, `db_mysql`, and `certbot` removed) with two
services added: `mcp-agent-skills` and `searxng`. Every service Dify
itself defines -- names, images, healthchecks, the shared `env_file`
anchors -- is otherwise untouched, deliberately, to keep upstream
upgrades a diffable, low-risk operation rather than a rewrite.

- `nginx` is the only service with a published port and the only thing
  end users talk to (it fronts both `web`, Dify's Studio frontend, and
  `api`). `mcp-agent-skills` and `searxng` are **internal-only, no
  published port** -- reachable only because they're on the same
  compose `default` network as `ssrf_proxy`, `api`, and `worker`. Never
  add a `ports:` entry to either.
- **Every outbound tool/MCP/HTTP-Request-node call from `api`/`worker`
  routes through the bundled `ssrf_proxy` (squid), which denies
  private-network destinations by default.** This is the single most
  important thing to know before touching MCP config here: without
  `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=mcp-agent-skills,searxng` in `.env`,
  Dify can register the MCP provider successfully (the API call
  succeeds) and still have every real tool call silently fail at the
  proxy layer. Nothing under `docker/` ships this allowlist by default --
  it was found by reading `docker/ssrf_proxy/docker-entrypoint.sh`'s
  `write_optional_private_allowlist`, not documented anywhere obvious.
- **MCP is wired in declaratively, via Dify's own console API, not by
  clicking "Add MCP Server" in Studio.** `scripts/bootstrap.sh` signs in
  as the admin account (cookie + CSRF token, matching how Dify's own
  frontend authenticates -- see that script's own comments for the exact
  flow, including why `ADMIN_API_KEY`-style bearer auth doesn't apply
  here) and `POST`s to
  `/console/api/workspaces/current/tool-provider/mcp` with
  `mcp-agent-skills`' URL. This is a real, first-party Dify API, not a
  workaround -- confirmed by reading
  `api/controllers/console/workspace/tool_providers.py` and
  `api/services/tools/mcp_tools_manage_service.py` directly. Unlike
  Open WebUI's/LibreChat's own agent-skills integrations, no custom
  bridge code was needed for the MCP connection itself.
- **Jira/Confluence credentials are ONE SHARED identity for this whole
  deployment, not per end-user -- a deliberate scope decision, confirmed
  necessary by reading the actual Dify source, not assumed.** Dify's own
  `MCPToolProvider.identity_mode` column supports exactly one
  non-`"off"` value (`idp_token`), which calls a Dify-Enterprise-only
  internal API (`/inner/api/mcp/issue-token`) to mint a per-user SSO
  token -- there is no Community Edition equivalent of LibreChat's
  `customUserVars` or Open WebUI's per-user Tool Valves for MCP tool
  providers. `JIRA_*`/`CONFLUENCE_*` in `.env` configure
  `mcp-agent-skills`' own process environment directly (unlike the
  Open WebUI branch's per-user design, `MCP_TRUST_REQUEST_CREDENTIALS`
  is NOT set here -- there is no per-request credential to trust). A
  genuine per-user upgrade later would mean writing a custom Dify plugin
  (Dify's own Plugin SDK, not a config change) -- out of scope here
  because it wasn't asked for and couldn't be verified without a live
  instance to test against.
- **Tool-call approval is one layer, not two, in this deployment.**
  `agent-skills`' own `--confirm` requirement (enforced in that repo's
  code, independent of any caller) is unchanged and is the only
  write-action gate here. `api/core/workflow/nodes/agent_v2/`'s
  `dangerous`/`requires_confirmation` flag handling looked promising on
  first read but turned out to gate a different thing (whether a
  locally-registered CLI tool is bootstrapped into an Agent Soul's
  toolset at config time, not a per-call runtime pause for an arbitrary
  MCP tool's result) -- don't assume it does what LibreChat's
  `toolApproval` or Open WebUI's `__event_call__` confirmation card did
  without re-verifying against a live instance first.
- **The OpenAI-API-compatible model provider is a plugin, not built-in
  code, in Dify 1.x** (`api/core/model_runtime/model_providers/` no
  longer exists in this version's `api/` codebase at all -- confirmed by
  its absence). Installing it requires either the Dify Marketplace
  (`marketplace.dify.ai`, needs a specific version+hash identifier this
  repo has no verified way to pin) or a GitHub-release install
  (`repo`/`version`/`package` fields, same problem: no verified current
  release asset name to hardcode). `scripts/bootstrap.sh` detects
  whether it's installed and configures your vLLM endpoint declaratively
  if so, otherwise prints the one-time manual Studio step
  (Plugins -> Marketplace -> search -> Install) -- this is an honest
  scope boundary, not a shortcut: shipping a guessed identifier that
  goes stale silently would be worse than one documented manual click.
- `agent-skills` is a **git submodule pinned to a released tag**, not a
  branch -- so this stack always states exactly which `agent-skills`
  version it's running. Bumping the pin is a reviewable commit; see
  [`docs/OPERATIONS.md`](docs/OPERATIONS.md) "Updating the agent-skills
  submodule" for the exact steps. There's no bind-mount for
  live-editing skills against this stack -- that's deliberate
  (reproducibility over iteration speed). `agent-skills/AGENTS.md`
  covers that submodule's own internals; once you're working inside
  `agent-skills/`, that file (closest-`AGENTS.md`-wins) governs, not
  this one.

## Security / credentials

- **Every secret lives in `.env` (gitignored) or `searxng/settings.yml`
  (also gitignored)** -- never in a tracked file. `.env.example` and
  `searxng/settings.yml.example` are the tracked templates, with
  placeholder values only (Dify's own upstream `.env.example` ships
  real-looking development defaults like `difyai123456` for several of
  these -- every one of them was replaced with a blank here specifically
  so nobody copies this repo's template straight into a real deployment
  with a publicly-known password already in it). `scripts/generate-secrets.sh`
  generates real values; never hand-write one into a template file.
- `docker-compose.yml` references secrets only as `${VAR}` env
  interpolation -- if you're about to write a literal secret value into
  it, stop; it belongs in `.env` instead.
- `SECRET_KEY` encrypts everything Dify stores in Postgres (model
  provider API keys, tool/MCP credentials). Restoring `volumes/` next to
  a *different* value makes stored credentials permanently undecryptable
  -- see [`docs/OPERATIONS.md`](docs/OPERATIONS.md) "Secrets" before
  touching backup/restore.
- Several other values must match each other across two variable names
  (`REDIS_PASSWORD`/`CELERY_BROKER_URL`, `PGVECTOR_PASSWORD`/
  `PGVECTOR_POSTGRES_PASSWORD`, `SANDBOX_API_KEY`/`CODE_EXECUTION_API_KEY`)
  because Dify's own config doesn't derive one from the other --
  `scripts/generate-secrets.sh` prints each pair already in sync; if you
  ever hand-edit one side, update the other too.

## Project map

- [`README.md`](README.md) -- start here: prerequisites, first run, what
  each service is, how the Jira/Confluence tools actually get used.
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) -- every `.env`
  variable this repo adds or changes from Dify's own default, the
  shared-vs-per-user credential model, and the tool-approval trade-off,
  all in detail.
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) -- backup/restore, upgrades,
  bumping the `agent-skills` submodule pin, restart ordering.
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) -- specific known
  failure modes (e.g. the MCP tool provider registering but every call
  failing at the SSRF proxy).
- `agent-skills/` -- git submodule, pinned to a tag (see Architecture
  above); has its own `AGENTS.md` and `AUTHENTICATION.md` governing that
  subtree.
