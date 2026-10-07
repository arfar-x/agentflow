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
| `MCP_TRUST_REQUEST_CREDENTIALS` | `1` to let the `agentflow-mcp-auth` plugin (see its own section below) override a caller's Jira/Confluence credentials per request. Trusted here on network isolation alone -- see that section for the reasoning. |
| `JIRA_BASE_URL` / `JIRA_USERNAME` / `JIRA_PASSWORD` | The **fallback** identity for any `jira_*` call from a Dify end user who hasn't set their own credentials via `agentflow-mcp-auth` -- see "Per-user vs. shared credentials" below. |
| `JIRA_AUTO_CONFIRM_WRITES` | Leave `false`. This is agent-skills' own write gate (enforced in its code) -- see "Tool approval" below; there's no second layer above it in this deployment, so this is the one thing standing between a model and an unconfirmed write. |
| `JIRA_DEFAULT_PROJECT` / `JIRA_DEPLOYMENT_TYPE` | Same as before -- see `skills/jira/README.md` in the submodule. |
| `CONFLUENCE_BASE_URL` / `CONFLUENCE_USERNAME` / `CONFLUENCE_PASSWORD` / `CONFLUENCE_AUTO_CONFIRM_WRITES` / `CONFLUENCE_DEFAULT_SPACE` | Same pattern as Jira above. |
| `CONFLUENCE_DEPLOYMENT_TYPE` | Required if `confluence` is in `MCP_TOOLSETS` -- `cloud` or `server`. |

### Per-user vs. shared credentials

LibreChat (`customUserVars`) and this project's Open WebUI branch (a
Tool's per-user Valves) both let each person chatting use their own Jira/
Confluence identity. **This Dify deployment now does too**, via
`dify-plugins/agentflow-mcp-auth` -- a Dify plugin this repo builds and
installs itself, not a Community Edition feature.

`api/models/tools.py`'s `MCPToolProvider.identity_mode` column (the
native Dify mechanism for this) supports exactly one non-`"off"` value,
`idp_token`, which calls a **Dify-Enterprise-only** internal endpoint
(`/inner/api/mcp/issue-token`). There is no Community Edition path to
"each caller's own secret gets forwarded to the MCP server" built into
Dify's own MCP tool-provider integration -- confirmed by reading that
source directly. That's why this is a separate plugin bridging the exact
same mechanism `agent-skills/AUTHENTICATION.md` (Part 2) already exposes
for any HTTP client, rather than a Dify config value: `mcp-agent-skills`
never needed to change, only something to call it per-user needed to
exist. See "agentflow-mcp-auth plugin" below for exactly how.

The shared `JIRA_*`/`CONFLUENCE_*` vars above are still meaningful: any
Dify end user who never sets their own credentials falls back to them, the
same one-shared-identity behavior this deployment always had. Leave them
blank instead if you want every user to be required to set their own.

### agentflow-mcp-auth plugin

Source: `dify-plugins/agentflow-mcp-auth`. Installed and configured
entirely by `scripts/bootstrap.sh` (package -> self-sign -> upload ->
install -> create its Endpoint -> configure its one provider setting) --
nothing here is a Studio-only manual step. Full design notes live in the
plugin's own `README.md`; this section only covers deployment-level
config and the trust model.

**What it does, in one sentence:** each Dify end user gets a one-time link
(`get_credentials_link` tool) to their own small HTML form (never through
chat) for entering their own Jira/Confluence username+password, encrypted
and stored per-user inside the plugin, then injected as
`X-Agent-Skills-Env-*` headers (`agent-skills/AUTHENTICATION.md` Part 2)
on every `mcp_call_tool` call that user makes.

**Signing, not a weaker `FORCE_VERIFYING_SIGNATURE`.** Self-hosted Dify
rejects any plugin package without a trusted signature by default. Rather
than setting `FORCE_VERIFYING_SIGNATURE=false` (which would accept *any*
unsigned plugin, Marketplace or not -- a real security regression for
this whole deployment, not just this one plugin), `scripts/bootstrap.sh`:

1. Generates its own keypair once (`dify signature generate`), kept at
   `volumes/plugin_signing/agentflow.{private,public}.pem` (gitignored;
   back it up like any other secret -- see `docs/OPERATIONS.md`
   "Secrets"; losing it just means the next `bootstrap.sh` run generates a
   new one and re-signs, nothing stored is encrypted with it).
2. Signs the packaged plugin with `-c community` (`dify signature sign`)
   -- deliberately not `-c langgenius`, which would make
   `ENFORCE_LANGGENIUS_PLUGIN_SIGNATURES` (still `true`, unchanged) reject
   it for not actually being signed by langgenius.
3. `docker-compose.yml`'s `plugin_daemon` service trusts *only* that one
   public key (`THIRD_PARTY_SIGNATURE_VERIFICATION_ENABLED=true`,
   `THIRD_PARTY_SIGNATURE_VERIFICATION_PUBLIC_KEYS=/app/keys/agentflow.public.pem`)
   -- every other plugin (Marketplace, langgenius-authored) still needs a
   real langgenius signature exactly as before this change.

**Trust model: network isolation, not inbound auth
(`agent-skills/AUTHENTICATION.md` Part 3/4a, deliberately).** The plugin
sets `MCP_TRUST_REQUEST_CREDENTIALS=1` on `mcp-agent-skills`, which prints
a startup warning ("a caller's self-asserted credential header will be
honored with nothing verifying who's actually calling") -- expected and
accounted for here, not a misconfiguration: `mcp-agent-skills` has no
published port and is reachable only from `api`/`worker`/`plugin_daemon`
on this compose project's own internal `default` network, so the header
is only ever set by this plugin's own server-side code, keyed off
`ToolRuntime.user_id` -- populated by Dify's own backend from the actual
authenticated caller, not a value the end user's chat message can
influence. Standing up Keycloak for Part 3's stronger guarantee (verifying
the *caller*, i.e. the plugin daemon itself, cryptographically) was
considered and deliberately skipped: it would only prove what network
isolation already guarantees here, for a real deployment cost (a new
service, a new credential to manage) this repo doesn't currently carry
anywhere else.

**Credentials never reach the LLM's context.** `get_credentials_link`
returns a link, not a form -- the human opens it in their own browser and
posts straight to the plugin's Endpoint. Verified live: the Endpoint
correctly serves the real form for a valid signed token and a "link
isn't usable" page for a forged/expired one.

**Every secret is encrypted at rest**, with a root key the plugin
generates for itself on first use and keeps in its own persistent
storage -- never a workspace-visible Studio credential field, never in
`.env`. See the plugin's own `README.md`/`PRIVACY.md` for the exact
mechanism.

**Verified against a live server, not just constructed:** calling
`jira_search` through the plugin's own MCP client with a per-request
`X-Agent-Skills-Env-JIRA_BASE_URL` header pointed at a nonexistent host
produced a connection error naming *that* host, not the deployment's real
configured Jira -- confirming the header genuinely overrides the shared
identity per call.

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
