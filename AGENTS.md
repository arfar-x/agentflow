# Agent instructions

## Overview

`agentflow` is a self-hosted, single-front-door agentic chatbot stack:
[LibreChat](https://www.librechat.ai/) as the one UI end users see, talking
to a self-hosted, OpenAI-compatible LLM endpoint (vLLM), with Jira (and
soon Confluence) exposed as tools via the `agent-skills` MCP server. It's
mostly an infrastructure repo -- a pinned `docker-compose.yml` plus
config/scripts -- with **one application module of its own, `kb/`**: the
knowledge catalog, built to a written spec
([`docs/spec/knowledge-base.md`](docs/spec/knowledge-base.md)) and served to
LibreChat as its own MCP server. Everything else here is configuration.
Nothing here names a specific model or organization; every such detail
lives in your own `.env`/`config/librechat.yaml`, not in this doc.

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
renders `config/librechat.yaml` from `config/librechat.yaml.example` +
`.env` (re-rendered every run -- see "Configuration is generated" below),
builds and starts every service, waits for `api` to report healthy, and
creates the admin account if none exists yet. Safe to rerun. Full variable
reference: [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

### Configuration is generated

`config/librechat.yaml` is gitignored and generated, the same way
`searxng/settings.yml` is: `make up` / `make render-config`
(`scripts/render-librechat-config.sh`) render it from
`config/librechat.yaml.example` + `.env`, filling in the `REPLACE_ME_*`
placeholders (currently `AGENTS_RECURSION_LIMIT`/
`AGENTS_MAX_RECURSION_LIMIT`). **Edit `config/librechat.yaml.example`, not
`config/librechat.yaml`** -- the latter is overwritten on the next render.
`docs/CONFIGURATION.md` "Agents" covers the two env variables; anything
else in the file (models, MCP, toolApproval) is edited directly in the
`.example` template, same as before this split existed.

## Day to day

```bash
make ps                              # health of all services
make logs SERVICE=api                # or any other service name
make build SERVICE=mcp-agent-skills  # rebuild one service's image
make down                            # stop (volumes kept)
make backup                          # snapshot every volume + .env
```

Run `make help` for the full target list -- `Makefile` is a thin wrapper
over `docker compose`/`scripts/`, nothing in it isn't documented in
[`README.md`](README.md) or [`docs/OPERATIONS.md`](docs/OPERATIONS.md) too.

User account management also goes through `make user-*` targets (create,
list, ban, invite, delete, reset-password) -- see README.md "Managing
users" for the full list and what each wraps. Role/permission management
(promoting someone to `ADMIN`, custom roles, delegated grants) is done in
the Admin Panel UI, not the CLI -- see README.md and
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) "Admin panel".

Agents are managed declaratively: `make agent-export` writes every agent
(definition, handoff/subagent links, sharing) from the database to
`agents/*.yaml`, and `make agent-import` (preview with `DRY_RUN=1`) applies
those files back. The files are the source of truth for the agents they list --
see [`docs/AGENT_SYNC.md`](docs/AGENT_SYNC.md).

## Testing

Two suites. `kb/` is this repo's own code and has real unit tests, which
need no services and run in well under a second:

```bash
cd kb && python -m pytest   # see kb/README.md for the venv setup
make kb-test                # the same suite plus the Postgres-backed tests,
                            # against a throwaway database container
```

They include two gates worth knowing about before you edit anything there:
`tests/test_boundaries.py` fails if `domain/` or `application/` imports
anything that does I/O, and `tests/test_spec_coverage.py` fails if a
requirement the spec marks `done` has no test claiming it (or a test claims
an id the spec doesn't define). Add the requirement to the spec first, then
the test's `# Covers: FR-...` line.

The other command runs `agent-skills`' own `mcp-server` test suite inside
the built image, as a way to verify that submodule pin is sound in this
stack's actual runtime:

```bash
make test
```

## Architecture

`docker-compose.yml` is the architecture -- read it directly for exact
service wiring, networks, and healthchecks; this is a summary, not a
substitute.

- `api` (LibreChat) is the only service with a published port and the
  only thing end users talk to. It calls `mongodb` (users/conversations/
  agent defs), `meilisearch` (search), `rag_api` + `vectordb` (per-
  conversation file RAG), `mcp-agent-skills` (Jira, Confluence, and other
  tools, over MCP), and `searxng` (native web search).
- `mcp-agent-skills`, `mcp-kb` and `searxng` are **internal-only, no
  published port, `backend` network only** -- reachability from `api` is
  their only access control (MCP's HTTP transport has no auth of its own).
  Never add a `ports:` entry to any of them. `mcp-kb` goes further and takes
  no credentials at all: the catalog is shared, so there is nothing per-user
  for a caller to supply.
- `kb-db` is the knowledge catalog's **own Postgres instance**, not another
  database inside `vectordb` -- so restoring LibreChat's RAG store can't touch
  the catalog, and `kb` never holds a credential to LibreChat's data. Schema and
  the least-privilege query role come from `make kb-init` (idempotent; also how
  a later migration is applied). Backed up like every other volume.
- `admin-panel` is a separate service (ClickHouse's LibreChat Admin
  Panel) that talks to `api`'s `/api/admin/*` endpoints -- it has no
  database access of its own and cannot grant itself privileges.
- **Tool approval is two-layered, deliberately**: LibreChat's own
  `toolApproval` (`config/librechat.yaml`) prompts before any tool call
  matching its `ask` list; independently, each write-capable toolset
  inside `agent-skills` (Jira and Confluence today) refuses to execute
  without its own `--confirm`, enforced in that repo's own code. Neither
  layer is a substitute for the other -- see that same file's inline comment for why
  the `ask` list uses LibreChat's real `<toolset>_<action>_mcp_<server>`
  tool-name format, not the colon-shaped form LibreChat's own docs show
  (confirmed wrong by reading the actual matching code).
- `agent-skills` is a **git submodule pinned to a released tag**, not a
  branch -- so this stack always states exactly which `agent-skills`
  version it's running. Bumping the pin is a reviewable one-line commit;
  see [`docs/OPERATIONS.md`](docs/OPERATIONS.md) "Updating the
  agent-skills submodule" for the exact steps. There's no bind-mount for
  live-editing skills against this stack -- that's deliberate
  (reproducibility over iteration speed). `agent-skills/AGENTS.md` covers
  that submodule's own internals; once you're working inside
  `agent-skills/`, that file (closest-`AGENTS.md`-wins) governs, not this
  one.

## Security / credentials

- **Every secret lives in `.env` (gitignored) or `searxng/settings.yml`
  (also gitignored)** -- never in a tracked file. `.env.example` and
  `searxng/settings.yml.example` are the tracked templates, with
  placeholder values only. `scripts/generate-secrets.sh` generates real
  values; never hand-write one into a template file.
- `config/librechat.yaml` and `docker-compose.yml` reference secrets only
  as `${VAR}` env interpolation -- if you're about to write a literal
  secret value into either, stop; it belongs in `.env` instead.
- Per-user Jira credentials (LibreChat's `customUserVars`) are injected
  per-request into `mcp-agent-skills` via `X-Agent-Skills-Env-<VAR>`
  headers, trusted only because `MCP_TRUST_REQUEST_CREDENTIALS=1` is safe
  specifically *here* (no published port, `api` is the only caller on the
  `backend` network) -- see `agent-skills/AUTHENTICATION.md` before
  changing anything in that trust chain.
- `CREDS_KEY`/`CREDS_IV`/`JWT_SECRET`/`JWT_REFRESH_SECRET` encrypt
  everything LibreChat stores in Mongo. Restoring `mongodb_data` next to
  a *different* set of these makes stored credentials permanently
  undecryptable -- see [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
  "Secrets" before touching backup/restore.

## Project map

- [`README.md`](README.md) -- start here: prerequisites, first run, what
  each service is.
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) -- every `.env`
  variable, what breaks if it's wrong.
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) -- backup/restore, upgrades,
  bumping the `agent-skills` submodule pin, restart ordering.
- [`docs/AGENT_SYNC.md`](docs/AGENT_SYNC.md) -- `make agent-export` /
  `agent-import`: agent file format, sharing, import semantics.
- `agents/` -- one YAML per agent, from `make agent-export` or hand-written;
  start from `agents/base-agent-template.yaml.example`.
- [`docs/KEYCLOAK.md`](docs/KEYCLOAK.md) -- optional SSO, and how to
  switch to/from local email/password auth.
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) -- specific known
  failure modes (e.g. MCP tools missing from the tool picker).
- `docs/spec/` -- the spec of record for anything in this repo big enough to
  design before building. A change to one of these is a reviewed diff, not a
  decision made in a chat window; the code follows the spec, and when they
  disagree one of them is wrong and gets fixed. Requirements carry ids and a
  status, and the owning module's tests enforce that pairing.
- `kb/` -- the knowledge catalog: what knowledge exists in the organization
  and where it lives, searchable by agents. Hexagonal (`domain/` ->
  `application/` -> `adapters/`), specified by
  [`docs/spec/knowledge-base.md`](docs/spec/knowledge-base.md); see
  [`kb/README.md`](kb/README.md) to run it.
- `agent-skills/` -- git submodule, pinned to a tag (see Architecture
  above); has its own `AGENTS.md` and `AUTHENTICATION.md` governing that
  subtree.
