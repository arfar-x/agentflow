# Configuration reference

This lists what `.env.example` adds or changes relative to Dify's own
upstream `docker/.env.example` (tag `1.17.1`) -- for every other tunable
(worker counts, timeouts, logging, ...), Dify's own defaults apply
unchanged; see `docker/.env.example` in
[langgenius/dify](https://github.com/langgenius/dify) for the complete
list. Copy `.env.example` to `.env`, fill these in, then `chmod 600 .env`.

## Secrets

Generate with `scripts/generate-secrets.sh` -- don't hand-write these.

| Variable | Notes |
|---|---|
| `SECRET_KEY` | Encrypts every credential Dify stores (model provider API keys, tool/MCP credentials). Rotating this on a live deployment makes existing stored credentials permanently unreadable. |
| `DB_PASSWORD` | Dify's own app database (`db_postgres`). |
| `REDIS_PASSWORD` + `CELERY_BROKER_URL` | Must match -- `CELERY_BROKER_URL` embeds the same password a second time; Dify doesn't derive one from the other. |
| `PGVECTOR_PASSWORD` + `PGVECTOR_POSTGRES_PASSWORD` | Must match -- one configures the `pgvector` container's own postgres user, the other is the app's connection string to it. |
| `PLUGIN_DAEMON_KEY` / `PLUGIN_DIFY_INNER_API_KEY` | Auth between `api`/`worker` and `plugin_daemon`. |
| `SANDBOX_API_KEY` + `CODE_EXECUTION_API_KEY` | Must match -- one configures the `sandbox` service, the other is how `api`/`worker` authenticate to it. |
| `DIFY_AGENT_API_TOKEN` / `DIFY_AGENT_SERVER_SECRET_KEY` | Bearer token and JWE key for the newer Agent Soul (`agent_backend`) runtime. |
| `DIFY_ADMIN_PASSWORD` | The one account `make up` creates automatically (see "Auth" below). |

**Rotating `SECRET_KEY` on a live deployment makes existing stored
credentials permanently unreadable.** See `docs/OPERATIONS.md`.

## Vector store (RAG)

| Variable | Notes |
|---|---|
| `VECTOR_STORE` | `pgvector` -- self-hosted, no external account, consistent with this repo's history. Dify supports many alternatives (Weaviate, Qdrant, Milvus, ...); this repo's `docker-compose.yml` only defines the `pgvector` one (see AGENTS.md "Architecture" for why the file was trimmed). |
| `PGVECTOR_HOST` / `PGVECTOR_PORT` / `PGVECTOR_USER` / `PGVECTOR_PASSWORD` / `PGVECTOR_DATABASE` | The app's own connection settings -- these have **no built-in default** in Dify's code (confirmed by reading `api/configs/middleware/vdb/pgvector_config.py`: every one of these fields defaults to `None`, and `PGVECTOR_PORT`'s Python default of `5433` is wrong for this compose file's `pgvector` service, which listens on the standard `5432`). Get these wrong and RAG/knowledge-base indexing fails outright, not gracefully. |
| `PGVECTOR_PGUSER` / `PGVECTOR_POSTGRES_PASSWORD` / `PGVECTOR_POSTGRES_DB` | The **container's own** provisioning vars (separate from the four above, which configure how the app connects to it) -- must describe the same instance from the other side. |

This is a **separate Postgres instance from `DB_*`** (Dify's own app
database) -- two Postgres-family containers, not one shared between
roles. That's Dify's own tested default shape for this profile, not
something agentflow merged; see `docs/OPERATIONS.md` for what each
volume is worth.

## MCP: agent-skills

| Variable | Notes |
|---|---|
| `MCP_TOOLSETS` | Default `jira`. **Quote it** if it lists more than one (`MCP_TOOLSETS="jira confluence"`). Space-separated toolset names, each needing `skills/<name>/requirements.txt` in the `agent-skills` submodule. Changing this requires `docker compose up -d --build mcp-agent-skills`. |
| `JIRA_BASE_URL` / `JIRA_USERNAME` / `JIRA_PASSWORD` | Required for every `jira_*` tool call -- see "Per-user vs. shared credentials" below for why these are filled in here at all, unlike the Open WebUI branch of this project. |
| `JIRA_AUTO_CONFIRM_WRITES` | Leave `false`. This is agent-skills' own write gate (enforced in its code) -- see "Tool approval" below; there's no second layer above it in this deployment, so this is the one thing standing between a model and an unconfirmed write. |
| `JIRA_DEFAULT_PROJECT` / `JIRA_DEPLOYMENT_TYPE` | Same as before -- see `skills/jira/README.md` in the submodule. |
| `CONFLUENCE_BASE_URL` / `CONFLUENCE_USERNAME` / `CONFLUENCE_PASSWORD` / `CONFLUENCE_AUTO_CONFIRM_WRITES` / `CONFLUENCE_DEFAULT_SPACE` | Same pattern as Jira above. |
| `CONFLUENCE_DEPLOYMENT_TYPE` | Required if `confluence` is in `MCP_TOOLSETS` -- `cloud` or `server`. |

### Per-user vs. shared credentials

LibreChat (`customUserVars`) and this project's Open WebUI branch (a
Tool's per-user Valves) both let each person chatting use their own Jira/
Confluence identity. **This Dify deployment does not** -- it's one shared
identity for every workspace member, configured once in `.env`, by
deliberate scope decision confirmed against Dify's own source, not a
workaround for a missing feature nobody looked for:

`api/models/tools.py`'s `MCPToolProvider.identity_mode` column supports
exactly one non-`"off"` value, `idp_token`, which calls a
**Dify-Enterprise-only** internal endpoint
(`/inner/api/mcp/issue-token`) to mint a per-user SSO access token and
stamp it on the outbound MCP request. There is no Community Edition path
to "each caller's own secret gets forwarded to the MCP server" the way
LibreChat's `customUserVars` or Open WebUI's Tool Valves work. Because of
that, `mcp-agent-skills` here runs the same way LibreChat's own
"AUTHENTICATION.md shape 3" describes: one identity, configured in the
server's own process environment, with `MCP_TRUST_REQUEST_CREDENTIALS`
**not** set (there's no per-request credential to trust in the first
place).

**Upgrading this to per-user later** would mean writing a Dify plugin
(Dify's own Plugin SDK packages a "tool provider," similar in spirit to
Open WebUI's Tool but implemented completely differently) that reads a
per-conversation or per-user input and forwards it as
`X-Agent-Skills-Env-*` headers itself -- a real, scoped engineering task,
not a config change, and out of scope for this deployment because it
wasn't asked for and couldn't be verified without a live Dify instance
to build and test the plugin against.

### Tool approval

**One layer, not two, in this deployment.** `mcp-agent-skills`'s own
`--confirm` requirement (`agent-skills`' code, unchanged, independent of
any caller) is the only gate standing between a model and an actual
Jira/Confluence write: a write tool refuses to execute unless its
arguments include `confirm: true`, which the model only sets after
telling the user what it's about to do (per that skill's own `SKILL.md`
rules, read via `get_skill`) and getting a yes.

There is **no verified, declarative Dify-native second layer** on top of
that in the Community Edition. `api/core/workflow/nodes/agent_v2/`'s
`dangerous`/`requires_confirmation`/`risk_level` flag handling looked like
exactly this at first read, but it gates whether a **locally-registered
CLI tool is bootstrapped into an Agent Soul's toolset at config time** --
not a per-call runtime pause showing an Allow/Deny card for an arbitrary
MCP tool's result, the way LibreChat's `toolApproval` or Open WebUI's
`__event_call__` confirmation did. Don't assume otherwise without
re-verifying against a live 1.17.1 instance first (build an app, attach
the MCP tool, trigger a write, and watch what actually happens).

If this single layer isn't enough for your risk tolerance, the honest
options are: keep `*_AUTO_CONFIRM_WRITES=false` (already the default) and
rely on the model's own prompted behavior plus this gate, or build the
per-user-plugin work described above and add real confirmation UI to it
at the same time (the two problems share a lot of the same plugin code).

## LLM endpoint (vLLM)

| Variable | Notes |
|---|---|
| `VLLM_BASE_URL` | OpenAI-compatible base URL, e.g. `https://vllm.internal/v1`. |
| `VLLM_API_KEY` | Passed as a Bearer token. |
| `VLLM_MODEL_NAME` | The model id your endpoint actually serves, e.g. `claude-sonnet-4-6` even if that's an opaque label over a different underlying model. |

Unlike every other stack this repo has run, model providers in Dify 1.x
are **plugins**, not built-in code or simple env vars --
`api/core/model_runtime/model_providers/` doesn't exist in this version's
codebase at all (confirmed by its absence; it moved to a separate plugin
package). `scripts/bootstrap.sh` handles what's actually declarative:

1. Checks whether the `langgenius/openai_api_compatible/openai_api_compatible`
   provider is installed (`GET /console/api/workspaces/current/model-providers`).
2. If it is, `POST`s your `VLLM_BASE_URL`/`VLLM_API_KEY`/`VLLM_MODEL_NAME`
   as a custom model credential (`.../models/credentials`) -- fully
   declarative, a real console API call.
3. If it isn't, it prints the one manual step and stops there.

**Installing the plugin itself is the one thing this repo could not
safely automate.** Both of Dify's own install paths (Marketplace, GitHub
release) need an exact version+hash identifier pinned ahead of time;
`marketplace.dify.ai`'s API wasn't reachable to verify one from this
environment, and guessing a GitHub release asset name that goes stale
silently would be worse than a documented manual click. Install it once:
**Studio -> Plugins -> Marketplace -> search "OpenAI-API-compatible" ->
Install**, then rerun `scripts/bootstrap.sh` (or `make up`) to configure
it declaratively. After that, setting it as the workspace default model
is also a one-time click: **Studio -> Settings -> Model Provider**.

## Auth

| Variable | Notes |
|---|---|
| `DIFY_ADMIN_EMAIL` / `DIFY_ADMIN_NAME` / `DIFY_ADMIN_PASSWORD` | The one account `make up`/`scripts/bootstrap.sh` creates via `/console/api/setup` on a fresh deployment (skipped once any account exists). Always the workspace owner -- the first account in a self-hosted Dify deployment always is. |
| `INIT_PASSWORD` | Optional gate in front of `/console/api/setup` itself -- leave blank for a normal deployment (bound to `127.0.0.1`, not publicly reachable before you finish setup); set it if this port might be reachable by someone else before you get to run `make up`. |

Inviting anyone beyond the first account is a Studio action, not a CLI
one -- see README.md "Managing workspace members". Dify's own SSO/OIDC
support for member login is gated behind Dify Enterprise
(`api/configs/enterprise/`), so there's no Keycloak walkthrough in this
version of the stack the way earlier LibreChat/Open WebUI branches had
one.
