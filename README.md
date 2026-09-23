# agentflow

Self-hosted, single-front-door agentic chatbot stack:
[LibreChat](https://www.librechat.ai/) as the one UI end users see, talking
to your own self-hosted, OpenAI-compatible LLM endpoint (vLLM), with Jira
and Confluence exposed as tools via the
[`agent-skills`](https://github.com/arfar-x/agent-skills) MCP server.

The first flow this stack supports end-to-end: a PM discusses a feature in
chat, pulls context from Jira, asks for a PRD, reviews and revises it in
conversation, and on approval publishes it -- with LibreChat's own tool
approval gate sitting in front of every write.

## What's running

| Service | What it is |
|---|---|
| `api` | LibreChat itself -- the chat UI and backend |
| `mongodb` | users, conversations, agent definitions |
| `meilisearch` | conversation search |
| `vectordb` (pgvector) | embeddings for per-conversation file uploads |
| `rag_api` | LibreChat's RAG sidecar, backed by `vectordb` |
| `mcp-agent-skills` | this repo's `agent-skills` submodule, built and served as an MCP server -- **internal only, no published port** |
| `admin-panel` | [ClickHouse/librechat-admin-panel](https://github.com/ClickHouse/librechat-admin-panel) -- users/groups/roles/grants UI, see "Managing users" below |
| `mcp-kb` | the knowledge catalog's MCP server (`kb_search`, `kb_get`) -- **internal only, no published port** |
| `kb-db` | the knowledge catalog's own Postgres (`kb/`, see [`docs/spec/knowledge-base.md`](docs/spec/knowledge-base.md)) -- **internal only, no published port** |
| `searxng` | self-hosted search backing LibreChat's native Web Search tool -- **internal only, no published port** |

Everything durable lives in named Docker volumes; every service config is a
mounted file. See [`docs/OPERATIONS.md`](docs/OPERATIONS.md) for what each
volume holds and how backup/restore works.

## Prerequisites

- Docker Engine + Compose v2
- A running, OpenAI-compatible vLLM endpoint (base URL + API key)
- Jira and/or Confluence credentials, if you want those tools live from
  the start (each user supplies their own via LibreChat's MCP Settings
  form -- see "Managing users" below)
- (Optional) An existing Keycloak instance, if you want SSO from day one --
  see [`docs/KEYCLOAK.md`](docs/KEYCLOAK.md)

## First run

```bash
git clone --recurse-submodules <this-repo-url> agentflow
cd agentflow
cp .env.example .env
scripts/generate-secrets.sh      # paste the output into .env
chmod 600 .env
```

Then fill in the rest of `.env` -- image tags, `VLLM_BASE_URL`/`VLLM_API_KEY`,
`JIRA_*`, `ADMIN_EMAIL`/`ADMIN_USERNAME`. Full reference:
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

```bash
make up
```

The one command that brings the whole stack up, every time -- first run or
the hundredth. Generates `searxng/settings.yml` from its template if it
doesn't exist yet, renders `config/librechat.yaml` from
`config/librechat.yaml.example` + `.env` (re-rendered every run, so it
always reflects your current `AGENTS_RECURSION_LIMIT`/
`AGENTS_MAX_RECURSION_LIMIT` etc.), builds and starts every service, waits
for `api` to actually be healthy, and creates the admin account (via
LibreChat's own `config/create-user.js`) if none exists yet -- prints the
login email and password when it does. Safe to rerun after a restart or
upgrade; it skips account creation once a user exists. (`scripts/bootstrap.sh` / `make
bootstrap` do the exact same thing -- `up` is just the name you already
reach for.) See [`docs/OPERATIONS.md`](docs/OPERATIONS.md) "First run" for
exactly what it does and doesn't do. Then open
`http://localhost:${PORT:-3080}` and log in.

## Day to day

```bash
make ps                        # health of all services
make logs SERVICE=api          # LibreChat logs
make logs SERVICE=mcp-agent-skills   # MCP server + toolset introspection logs
make build SERVICE=mcp-agent-skills  # after bumping the agent-skills submodule
make backup                    # snapshot every volume + .env
```

`make help` lists every target. See `Makefile` -- it's a thin wrapper over
`docker compose`/`scripts/`, nothing it does isn't documented elsewhere too.

## Managing users

New accounts don't come through the admin panel (it manages *existing*
accounts, not creation) -- `ALLOW_REGISTRATION=false` in this deployment,
so create them with the same officially-shipped LibreChat tool
`make up` uses for the admin account:

```bash
make user-create EMAIL=a@b.com NAME="A B" USERNAME=ab   # PASSWORD=... optional, generated if omitted
make user-list
make user-ban EMAIL=a@b.com MINUTES=60
make user-invite EMAIL=a@b.com     # email link instead -- needs email sending configured
make user-delete EMAIL=a@b.com     # interactive, asks you to confirm -- irreversible
make user-reset-password           # interactive (email + new password prompts)
```

For **roles and permissions** -- promoting someone to `ADMIN`, creating a
custom role, granting a specific admin capability like `manage:mcpservers`
without making someone a full admin, or building groups -- use the **Admin
Panel** at `http://localhost:${ADMIN_PANEL_PORT:-3000}` (log in with an
existing `ADMIN`-role account). See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) "Auth" for how admin access
is granted (the first user registered in this single-tenant deployment is
auto-admin) and the [LibreChat Admin Panel
docs](https://www.librechat.ai/docs/features/admin_panel) for the full
feature set.

## Managing agents

Agents (instructions, tools, handoffs/delegation and who they're shared with)
live in LibreChat's database. Mirror them to files, and back, with:

```bash
make agent-export              # database -> agents/*.yaml
make agent-import DRY_RUN=1    # preview
make agent-import              # agents/*.yaml -> database (OWNER_EMAIL=a@b.com forces the owner)
```

See [`docs/AGENT_SYNC.md`](docs/AGENT_SYNC.md) for the file format, what an
import does and doesn't touch, and how to restore onto another deployment.

## Where things live

```
agentflow/
├── docker-compose.yml       # the whole stack
├── config/librechat.yaml.example  # model + MCP + tool-approval config template -- rendered to config/librechat.yaml (gitignored) by `make up`
├── kb/                      # the knowledge catalog module (its own tests: make kb-test)
├── agent-skills/            # git submodule -> arfar-x/agent-skills, pinned to a tag
├── agents/                  # one YAML per agent (`make agent-export`, or write your own from base-agent-template.yaml.example)
├── mongo-init/              # declarative Mongo app-user creation (official mongo image convention)
├── scripts/                 # bootstrap, secret generation, backup, restore, agent sync
└── docs/
    ├── AGENT_SYNC.md        # agent export/import: file format and semantics
    ├── CONFIGURATION.md     # every .env variable, what breaks if it's wrong
    ├── KEYCLOAK.md          # SSO setup and how to switch to/from it
    ├── OPERATIONS.md        # backups, upgrades, submodule bumps, restart order
    └── TROUBLESHOOTING.md   # the specific failure modes this stack is known to hit
```

## Design notes worth knowing before you touch this

- **`mcp-agent-skills` has no authentication and no published port, on
  purpose.** The MCP HTTP transport doesn't authenticate callers at all;
  network isolation on the `backend` compose network is the only thing
  standing between "any tool" and "public internet." Never add a `ports:`
  entry to it.
- **`agent-skills` is a submodule pinned to a tag, not a branch.** Skill
  changes happen in that repo; bumping the pin here is a reviewable,
  one-line commit (`docs/OPERATIONS.md` has the exact steps). There is no
  bind-mount for live-editing skills against this stack -- that's a
  deliberate tradeoff for reproducibility over iteration speed.
- **If your vLLM endpoint advertises a model id that doesn't match the
  model actually being served** (common with some hosting setups),
  `config/librechat.yaml` treats that id as an opaque label rather than
  inferring context length/pricing/behavior from it -- see
  `docs/CONFIGURATION.md` for how that's configured explicitly instead.
- **Approval is two-layered, deliberately.** LibreChat's own
  `toolApproval` (in `config/librechat.yaml`) prompts before any tool call;
  each write-capable toolset in `agent-skills` (Jira and Confluence today)
  additionally refuses to execute without its own `--confirm`, enforced in
  code, not just in a prompt. Neither layer alone is a substitute for the
  other.

## Not yet in this stack

- **n8n**, for event-driven flows with no user in the loop (e.g. a
  Sentry-triage workflow) -- deliberately out of scope for this launch, and
  architecturally independent of everything here.
