# Configuration reference

Every variable `.env.example` declares, what it's for, and what breaks if
it's wrong or missing. Copy `.env.example` to `.env`, fill these in, then
`chmod 600 .env`.

## LibreChat / port

| Variable | Required | Default | Notes |
|---|---|---|---|
| `PORT` | no | `3080` | Port `api` listens on, bound to `127.0.0.1` only -- put a reverse proxy in front for TLS. |
| `LIBRECHAT_IMAGE_TAG` | yes | none | Pin a real published tag before production. `latest` on a dev channel moves under you without warning. |
| `RAG_API_IMAGE_TAG` | yes | none | Same reasoning as above, for the RAG sidecar. |

## Secrets

Generate with `scripts/generate-secrets.sh` -- don't hand-write these.

| Variable | Required | Notes |
|---|---|---|
| `CREDS_KEY` | yes | 32-byte hex. Encrypts every credential LibreChat stores (API keys, MCP tokens, etc). |
| `CREDS_IV` | yes | 16-byte hex, paired with `CREDS_KEY`. |
| `JWT_SECRET` | yes | Signs user session tokens. |
| `JWT_REFRESH_SECRET` | yes | Signs refresh tokens. |

**Rotating any of the four on a live deployment makes existing stored
credentials permanently unreadable.** They don't get re-encrypted -- they
just stop decrypting. See `docs/OPERATIONS.md`.

## MongoDB

| Variable | Required | Notes |
|---|---|---|
| `MONGO_ROOT_USERNAME` / `MONGO_ROOT_PASSWORD` | yes | Root credentials for the `mongodb` container itself. This compose file runs Mongo with `--auth` -- LibreChat's own upstream compose does not, by default. |
| `MONGO_APP_USERNAME` / `MONGO_APP_PASSWORD` | yes | The app-level user LibreChat authenticates as day to day. Create this user once, inside Mongo, the first time the stack comes up -- see `docs/OPERATIONS.md` "First run". |
| `MONGO_URI` | yes | Built from the two app vars above; leave the template in `.env.example` as-is unless you changed the database name. |

## Meilisearch

| Variable | Required | Notes |
|---|---|---|
| `MEILI_MASTER_KEY` | yes | Any client that can reach `meilisearch:7700` without this key gets nothing -- it's what keeps the search index from being an open door on the backend network. |

## pgvector (RAG for ad-hoc user uploads)

| Variable | Required | Notes |
|---|---|---|
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | yes | Backing store for LibreChat's built-in RAG API -- per-conversation file uploads. |
| `RAG_EMBEDDINGS_PROVIDER` | yes | `openai` works against any OpenAI-compatible endpoint, including your vLLM one. |
| `RAG_EMBEDDINGS_MODEL` | yes | Must be a model your `VLLM_BASE_URL` endpoint actually serves. If you haven't stood up a dedicated embeddings model yet, do that before enabling RAG uploads -- a chat model is not an embedding model. |

## LLM endpoint (vLLM)

| Variable | Required | Notes |
|---|---|---|
| `VLLM_BASE_URL` | yes | OpenAI-compatible base URL, e.g. `https://vllm.internal/v1`. |
| `VLLM_API_KEY` | yes (or blank if your endpoint has none) | Passed as a Bearer token. |

**The advertised-model-id trap:** some vLLM setups advertise a model `id`
that doesn't match the model actually being served (this deployment's
endpoint, for example, advertises `id: "claude-sonnet-4-6"` for a
different underlying model). `config/librechat.yaml` treats that string as
an opaque label -- `fetch: false` so LibreChat never tries to
auto-detect capabilities from it, and an explicit `tokenConfig` entry
declaring the real context window instead of whatever LibreChat would
otherwise assume from the name (e.g. one starting with `claude-`). If you
rename the served model on the vLLM side, update both the
`models.default` entry and the `tokenConfig` key in `config/librechat.yaml`
to match.

## MCP: agent-skills

| Variable | Required | Notes |
|---|---|---|
| `MCP_TOOLSETS` | no | Default `jira`. **Quote it** if it lists more than one (`MCP_TOOLSETS="jira confluence"`) -- `.env` is also `source`d directly by `scripts/bootstrap.sh`/`make up`, and an unquoted space-separated value breaks that (bash tries to run the second word as a command). Space-separated toolset names, each needing `skills/<name>/requirements.txt` to exist in the `agent-skills` submodule. Changing this requires `docker compose up -d --build mcp-agent-skills`. |
| `JIRA_BASE_URL` / `JIRA_USERNAME` / `JIRA_PASSWORD` | no (server-level) | Left blank in this deployment by design -- every Jira tool call's base URL and credential comes per-user instead, via LibreChat's `customUserVars` (`config/librechat.yaml`, injected as `X-Agent-Skills-Env-*` headers). Set these here only if you want a shared fallback identity instead of per-user. |
| `JIRA_AUTO_CONFIRM_WRITES` | no | Leave `false` in production. LibreChat's own `toolApproval` gate (in `config/librechat.yaml`) is the intended approval surface; this variable existing at all is an escape hatch for automation you explicitly trust, not something to flip for convenience. |
| `JIRA_DEFAULT_PROJECT` | no | Only used by `jira_triage`, and (in this deployment) supplied per-user via `customUserVars` like the credential -- see above. |
| `JIRA_DEPLOYMENT_TYPE` | no | Only required the first time `create_issue`/`edit_issue` sets an assignee. |
| `CONFLUENCE_BASE_URL` / `CONFLUENCE_USERNAME` / `CONFLUENCE_PASSWORD` | no (server-level) | Same per-user pattern as Jira above -- left blank here, supplied per-user via `customUserVars`. |
| `CONFLUENCE_AUTO_CONFIRM_WRITES` | no | Same reasoning as `JIRA_AUTO_CONFIRM_WRITES` -- leave `false`. |
| `CONFLUENCE_DEFAULT_SPACE` | no | Used by `my_pages`/`get_page_by_title` when no `--space_key` is given; supplied per-user via `customUserVars` in this deployment. |
| `CONFLUENCE_DEPLOYMENT_TYPE` | yes, if `confluence` is in `MCP_TOOLSETS` | `cloud` or `server` -- unlike Jira, this one is required: Confluence's REST API is mounted at a different path per deployment (Cloud: `/wiki/rest/api`, Server/DC: `/rest/api`). Server-level, not per-user -- the whole org's Confluence instance is one deployment type. |

## Auth

Two mutually exclusive modes -- see `docs/KEYCLOAK.md` for the full Keycloak
walkthrough and how to switch between them.

| Variable | Notes |
|---|---|
| `ALLOW_EMAIL_LOGIN` | `true` for local auth (default, simplest to start with). |
| `ALLOW_REGISTRATION` | Keep `false` even under local auth unless you specifically want open self-registration. New accounts normally come from `make user-create` instead -- see README.md "Managing users". |
| `ADMIN_EMAIL` / `ADMIN_NAME` / `ADMIN_USERNAME` / `ADMIN_PASSWORD` | The one account `make up`/`scripts/bootstrap.sh` creates automatically on a fresh deployment (skipped once any user exists). Auto-promoted to LibreChat's `ADMIN` role because it's the first user registered in this unscoped, single-tenant deployment -- see [Access Control](https://www.librechat.ai/docs/features/access_control#built-in-system-roles). Promoting anyone else to `ADMIN` afterwards is done from the Admin Panel (below), not by editing `.env`. |
| `OPENID_*` (commented out by default) | The whole Keycloak OIDC block. Uncommenting it and setting `ALLOW_EMAIL_LOGIN=false` switches auth modes on the next `api` restart -- no rebuild. |

## Admin panel

Separate `admin-panel` service ([ClickHouse/librechat-admin-panel](https://github.com/ClickHouse/librechat-admin-panel)) -- users/groups/roles/system-grants UI, at `http://localhost:${ADMIN_PANEL_PORT:-3000}`. It manages *existing* accounts (promote to `ADMIN`, build custom roles, delegate specific admin capabilities, create groups); it does not create accounts -- see README.md "Managing users" for that.

| Variable | Required | Notes |
|---|---|---|
| `ADMIN_PANEL_SESSION_SECRET` | yes | Min 32 chars, generated by `scripts/generate-secrets.sh`. The panel refuses to start without it. |
| `ADMIN_PANEL_PORT` | no | Default `3000`. Host port the panel is published on, bound to `127.0.0.1` same as `api`. |

## Agent import (optional)

Read by `make agent-import` (`scripts/agents.sh`) from `.env`, or from the
command line, where they win. None are needed for a normal import; they let one
set of committed agent files fit a differently-configured deployment. Reference:
[`docs/AGENT_SYNC.md`](AGENT_SYNC.md).

| Variable | Required | Notes |
|---|---|---|
| `OWNER_EMAIL` | no | Own every imported agent as this account, which must already exist. Otherwise an agent keeps its current owner, or gets `ADMIN_EMAIL`, or the oldest admin. |
| `MODEL_PROVIDER` | no | Provider for every imported agent, instead of the one in each file. |
| `MODEL_NAME` | no | Model for every imported agent, instead of the one in each file. |

Without the two model variables, a file's provider/model that don't exist on
this instance fall back to the instance default, and the import lists them to
review in the UI.
