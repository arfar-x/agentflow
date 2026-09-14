# agentflow

Self-hosted, single-front-door agentic chatbot stack:
[Open WebUI](https://openwebui.com/) as the one UI end users see, talking
to your own self-hosted, OpenAI-compatible LLM endpoint (vLLM), with Jira
and Confluence exposed as tools via the
[`agent-skills`](https://github.com/arfar-x/agent-skills) MCP server.

The first flow this stack supports end-to-end: a PM discusses a feature in
chat, pulls context from Jira, asks for a PRD, reviews and revises it in
conversation, and on approval publishes it -- with a confirmation card in
front of every write.

## What's running

| Service | What it is |
|---|---|
| `open-webui` | The chat UI and backend -- also serves the built-in Admin Panel (users/groups/roles/model config) at `/admin`, no separate service needed |
| `postgres` | Open WebUI's app database (users/conversations/chats) **and** its pgvector store for per-conversation file RAG -- one database, two roles |
| `mcp-agent-skills` | this repo's `agent-skills` submodule, built and served as an MCP server -- **internal only, no published port** |
| `searxng` | self-hosted search backing Open WebUI's native Web Search -- **internal only, no published port** |

Jira/Confluence are wired in as a native Open WebUI **Tool**
(`config/tools/agent_skills.py`, pushed into Open WebUI by
`scripts/bootstrap.sh`), not a raw MCP connection -- see "Design notes"
below for why, and [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) "Tool
approval" for the confirmation model.

Everything durable lives in named Docker volumes; every service config is
a mounted file or a bootstrapped API call. See
[`docs/OPERATIONS.md`](docs/OPERATIONS.md) for what each volume holds and
how backup/restore works.

## Prerequisites

- Docker Engine + Compose v2
- `jq` on the host (used by `scripts/bootstrap.sh` and the `make user-*`
  targets to talk to Open WebUI's own REST API)
- A running, OpenAI-compatible vLLM endpoint (base URL + API key)
- Jira and/or Confluence credentials, if you want those tools live from
  the start (each user supplies their own via a Tool's per-user Valves --
  see "Managing users" below)
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

Then fill in the rest of `.env` -- image tag, `VLLM_BASE_URL`/`VLLM_API_KEY`,
`JIRA_*`/`CONFLUENCE_*`, `WEBUI_ADMIN_EMAIL`. Full reference:
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

```bash
make up
```

The one command that brings the whole stack up, every time -- first run or
the hundredth. Generates `searxng/settings.yml` from its template if it
doesn't exist yet, builds and starts every service, waits for `open-webui`
to actually be healthy (which itself creates the admin account, from
`WEBUI_ADMIN_EMAIL`/`WEBUI_ADMIN_NAME`/`WEBUI_ADMIN_PASSWORD` in `.env`,
if none exists yet), then signs in as that admin and pushes
`config/tools/agent_skills.py` into Open WebUI as a Tool every user can
use. Safe to rerun after a restart or upgrade -- account creation is a
no-op once a user exists, and re-pushing the Tool's content keeps it in
sync with this repo on every run. See [`docs/OPERATIONS.md`](docs/OPERATIONS.md) "First
run" for exactly what it does and doesn't do. Then open
`http://localhost:${PORT:-3080}` and log in.

## Day to day

```bash
make ps                              # health of all services
make logs SERVICE=open-webui         # or any other service name
make logs SERVICE=mcp-agent-skills   # MCP server + toolset introspection logs
make build SERVICE=mcp-agent-skills  # after bumping the agent-skills submodule
make backup                          # snapshot every volume + .env
```

`make help` lists every target. See `Makefile` -- it's a thin wrapper over
`docker compose`/`scripts/`, nothing it does isn't documented elsewhere too.

## Managing users

New accounts don't come through open self-registration --
`ENABLE_SIGNUP=false` in this deployment, so create them the same way
`make up` creates the admin account, through Open WebUI's own REST API:

```bash
make user-create EMAIL=a@b.com NAME="A B"   # PASSWORD=... optional, generated if omitted
make user-list
make user-ban EMAIL=a@b.com     # blocks access -- NOT time-limited, see docs/CONFIGURATION.md
make user-unban EMAIL=a@b.com
make user-delete EMAIL=a@b.com     # interactive, asks you to confirm -- irreversible
make user-reset-password EMAIL=a@b.com   # PASSWORD=... optional, generated if omitted
```

Once an account exists, each user connects their own Jira/Confluence
identity themselves, in the UI: **Workspace -> Tools -> the wrench icon on
"Agent Skills (Jira & Confluence)" -> Valves**, and fills in their own
`JIRA_USERNAME`/`JIRA_PASSWORD`/etc. Nobody else's chat ever uses that
credential, and agent-skills' own audit trail on Jira/Confluence shows the
real person, not a shared service account -- see
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) "Per-user Jira/Confluence
credentials".

For **roles and permissions** -- promoting someone to admin, building
groups, delegating a specific admin capability -- use the **Admin Panel**,
built into Open WebUI itself at `http://localhost:${PORT:-3080}/admin`
(log in with an existing admin account). See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) "Auth" for how admin access
is granted (the first account ever created in this deployment is
auto-admin).

## Where things live

```
agentflow/
├── docker-compose.yml            # the whole stack
├── config/tools/agent_skills.py  # the Jira/Confluence Tool -- pushed into Open WebUI by scripts/bootstrap.sh
├── agent-skills/                 # git submodule -> arfar-x/agent-skills, pinned to a tag
├── scripts/                      # bootstrap, secret generation, backup, restore, user admin
└── docs/
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
- **Jira/Confluence are wired in as a Tool, not a raw MCP connection --
  deliberately.** Open WebUI's native MCP support forwards a caller's
  *identity* headers, never a per-user secret a user typed into a form,
  so it can't carry a different Jira/Confluence credential per user by
  itself. `config/tools/agent_skills.py` is a small bridge: its
  per-user Valves are exactly LibreChat's old `customUserVars` (same 8
  fields), and it calls `mcp-agent-skills` directly over MCP, injecting
  those Valves as `X-Agent-Skills-Env-*` headers -- see
  [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) "Per-user
  Jira/Confluence credentials" for the full reasoning.
- **`agent-skills` is a submodule pinned to a tag, not a branch.** Skill
  changes happen in that repo; bumping the pin here is a reviewable,
  one-line commit (`docs/OPERATIONS.md` has the exact steps). Because
  `config/tools/agent_skills.py` hand-lists agent-skills' current tool
  names rather than auto-discovering them, bumping the pin to a version
  that adds a new Jira/Confluence action also means adding one
  corresponding method to that file -- see its own top-of-file comment.
- **If your vLLM endpoint advertises a model id that doesn't match the
  model actually being served** (common with some hosting setups), give
  it a real display name once in Admin Settings -> Models -- see
  `docs/CONFIGURATION.md` for the exact steps.
- **Write actions are gated twice, deliberately.** Every
  `jira_*`/`confluence_*` write method in `config/tools/agent_skills.py`
  shows an Allow/Deny card before running, regardless of Open WebUI's own
  (per-user, switchable) Tool Permissions setting; each write-capable
  toolset in `agent-skills` (Jira and Confluence today) additionally
  refuses to execute without its own `--confirm`, enforced in code, not
  just a prompt. Neither layer alone is a substitute for the other.

## Not yet in this stack

- **n8n**, for event-driven flows with no user in the loop (e.g. a
  Sentry-triage workflow) -- deliberately out of scope for this launch, and
  architecturally independent of everything here.
