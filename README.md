# agentflow

Self-hosted, single-front-door agentic chatbot stack:
[Dify](https://dify.ai/) (Community Edition, pinned to `1.17.1`) as the
platform end users build and use chat apps in, talking to your own
self-hosted, OpenAI-compatible LLM endpoint (vLLM), with Jira and
Confluence exposed as tools via the
[`agent-skills`](https://github.com/arfar-x/agent-skills) MCP server,
registered with Dify **declaratively** -- as a real config API call this
repo scripts, not a form someone fills in by hand.

## What's running

Dify's own official multi-service deployment (`docker/docker-compose.yaml`
in [langgenius/dify](https://github.com/langgenius/dify), tag `1.17.1`),
trimmed to the one vector-store backend this repo uses, plus this stack's
own two services:

| Service | What it is |
|---|---|
| `nginx` | the only published port -- reverse-proxies `web` (Studio UI) and `api`/`api` websockets |
| `web` | Dify's Next.js frontend (Studio: build apps, chat, manage tools/models) |
| `api` | Dify's backend -- apps, conversations, tool/model configuration, the console API `scripts/bootstrap.sh` drives |
| `worker` / `worker_beat` | Celery workers for indexing, scheduled jobs, async app runs |
| `plugin_daemon` | runs installed plugins (model providers, tools) -- Dify 1.x moved these out of `api` into their own runtime |
| `sandbox` / `local_sandbox` | sandboxed Python code execution for the Code tool / Agent Soul |
| `agent_backend` / `agent_ssrf_proxy` | the newer "Agent Soul" (agent_v2) runtime and its own egress proxy |
| `ssrf_proxy` | outbound HTTP proxy every tool/MCP/HTTP-Request-node call routes through -- denies private-network destinations by default (see `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS` below) |
| `db_postgres` | Dify's own app database (users, apps, conversations) |
| `pgvector` | separate Postgres+pgvector instance, Dify's RAG/knowledge-base vector store |
| `redis` | cache + Celery broker/backend |
| `mcp-agent-skills` | this repo's `agent-skills` submodule, built and served as an MCP server -- **internal only, no published port** |
| `searxng` | self-hosted search, available to wire into a Dify tool/workflow -- **internal only, no published port** |

Everything durable lives under `./volumes/` (Dify's own bind-mount
convention, kept as-is rather than converted to named Docker volumes --
see `docs/OPERATIONS.md`). Every config that isn't pushed via API is a
mounted file (`nginx/`, `ssrf_proxy/`, `pgvector/`, `searxng/`).

## Prerequisites

- Docker Engine + Compose v2
- `jq` on the host (used by `scripts/bootstrap.sh` to talk to Dify's own console API)
- A running, OpenAI-compatible vLLM endpoint (base URL + API key)
- Jira and/or Confluence credentials, if you want those tools live from the start

## First run

```bash
git clone --recurse-submodules <this-repo-url> agentflow
cd agentflow
cp .env.example .env
scripts/generate-secrets.sh      # paste the output into .env
chmod 600 .env
```

Then fill in the rest of `.env` -- `VLLM_BASE_URL`/`VLLM_API_KEY`/
`VLLM_MODEL_NAME`, `JIRA_*`/`CONFLUENCE_*`, `DIFY_ADMIN_EMAIL`. Full
reference: [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

```bash
make up
```

Generates `searxng/settings.yml` from its template if it doesn't exist
yet, builds and starts every service, waits for `api` to actually be
healthy, creates the admin account (from `DIFY_ADMIN_EMAIL`/
`DIFY_ADMIN_NAME`/`DIFY_ADMIN_PASSWORD`) via Dify's own
`/console/api/setup` if none exists yet, then **registers
`mcp-agent-skills` as a Dify MCP tool provider declaratively** and, if the
OpenAI-API-compatible model plugin is already installed, configures your
vLLM endpoint as a model too. Safe to rerun. See
[`docs/OPERATIONS.md`](docs/OPERATIONS.md) "First run" for exactly what it
does and doesn't do, and [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md)
"LLM endpoint" for the one manual step it can't safely automate (installing
that plugin -- no verified marketplace identifier to pin).

Then open `http://localhost:${EXPOSE_NGINX_PORT:-80}` and log in.

## Day to day

```bash
make ps                              # health of all services
make logs SERVICE=api                # or any other service name
make logs SERVICE=mcp-agent-skills   # MCP server + toolset introspection logs
make build SERVICE=mcp-agent-skills  # after bumping the agent-skills submodule
make backup                          # snapshot volumes/ + .env
```

`make help` lists every target. See `Makefile` -- it's a thin wrapper over
`docker compose`/`scripts/`, nothing it does isn't documented elsewhere too.

## Managing workspace members

Dify has no CLI for this -- the account `make up` creates is the first
member (an owner); invite everyone else from inside Studio:
**Settings -> Members -> Invite Members** (email + role: admin / editor /
normal / dataset operator). See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) "Auth" for what each role
can do.

## Using the Jira/Confluence tools

1. Confirm the MCP provider loaded: **Studio -> Tools -> MCP -> Agent
   Skills (Jira & Confluence)** should show its tool list (`jira_*`,
   `confluence_*`, `doc_gen`, `get_skill`, `list_skills`).
2. Create an app (**Chatflow** or **Agent**), pick the model
   `scripts/bootstrap.sh` configured (or add one by hand), attach the
   Agent Skills MCP tools, and publish it.
3. Jira/Confluence credentials are **one shared identity for this whole
   deployment** (`JIRA_*`/`CONFLUENCE_*` in `.env`), not per end-user --
   see [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) "Per-user vs.
   shared credentials" for why, and what upgrading to per-user would need.

## Where things live

```
agentflow/
├── docker-compose.yml       # Dify's own compose, trimmed + this repo's 2 services
├── nginx/                   # Dify's nginx templates, unmodified
├── ssrf_proxy/               # Dify's squid templates, unmodified (allowlisting is env-driven)
├── pgvector/                 # Dify's pgvector init script, unmodified
├── agent-skills/             # git submodule -> arfar-x/agent-skills, pinned to a tag
├── scripts/                  # bootstrap, secret generation, backup, restore
└── docs/
    ├── CONFIGURATION.md     # every .env variable that isn't Dify's own upstream default, what breaks if it's wrong
    ├── OPERATIONS.md        # backups, upgrades, submodule bumps, restart order
    └── TROUBLESHOOTING.md   # the specific failure modes this stack is known to hit
```

## Design notes worth knowing before you touch this

- **`mcp-agent-skills` and `searxng` have no authentication and no
  published port, on purpose.** MCP's HTTP transport doesn't authenticate
  callers at all; network isolation on the compose `default` network
  (where `ssrf_proxy` -- the thing that actually dials out for every tool
  call -- also lives) is the only access control either has. Never add a
  `ports:` entry to either.
- **Every outbound tool/MCP call is denied by default, including to
  `mcp-agent-skills` itself** -- Dify's bundled `ssrf_proxy` (squid) blocks
  private-network destinations unless explicitly allowed.
  `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=mcp-agent-skills,searxng` in
  `.env.example` is what makes the MCP tool actually reachable; this isn't
  optional polish, it's why the declarative MCP registration works at all.
- **`docker-compose.yml` is Dify's own generated file, hand-trimmed, not
  regenerated from their `docker-compose-template.yaml`.** Every
  vector-store backend besides `pgvector`, `db_mysql`, and `certbot` were
  removed for a smaller, more legible file specific to this deployment;
  everything else (service names, images, healthchecks, the shared
  `env_file` anchors) is untouched. Bumping Dify's own version means
  re-diffing against a fresh copy of their compose file -- see
  [`docs/OPERATIONS.md`](docs/OPERATIONS.md) "Upgrades".
- **Jira/Confluence credentials are one shared identity, not per-user --
  a deliberate scope decision for this deployment**, not a limitation
  discovered too late. Dify's own MCP tool providers only support a
  single static credential/header set in the Community Edition (per-user
  identity forwarding is an Enterprise-only feature); see
  `docs/CONFIGURATION.md` for the reasoning and what a future per-user
  upgrade would actually require (a custom Dify plugin, not a config
  change).
- **Tool-call approval is one layer here, not two.** `agent-skills`' own
  `--confirm` requirement (enforced in that repo's code, independent of
  any caller) is unchanged and is the write-action gate in this
  deployment. No verified, declarative Dify-native equivalent of
  LibreChat's `toolApproval`/Open WebUI's per-call confirmation card was
  found for MCP tool results in the Community Edition -- see
  `docs/CONFIGURATION.md` "Tool approval" before assuming otherwise.
- **`agent-skills` is a submodule pinned to a tag, not a branch.** Skill
  changes happen in that repo; bumping the pin here is a reviewable
  commit -- see [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Not yet in this stack

- **Per-user Jira/Confluence credentials** -- see "Design notes" above.
- **A published, ready-to-use chat app** -- `scripts/bootstrap.sh` wires up
  the model and the MCP tool provider declaratively, but creating and
  publishing the actual Chatflow/Agent app that uses them is a one-time
  Studio step (2-3 minutes) documented in "Using the Jira/Confluence
  tools" above -- no verified DSL YAML for this was safe to ship
  unverified against a live instance.
- **n8n**, for event-driven flows with no user in the loop -- deliberately
  out of scope for this launch, and architecturally independent of
  everything here.
