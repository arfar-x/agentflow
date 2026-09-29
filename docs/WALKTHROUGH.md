# Walkthrough: running the stack from nothing

A single linear path from an empty checkout to a working chat with a filled
knowledge base, with the command to run and the output it actually produces
at every step. This doesn't replace the other docs -- it's the order to read
them in. Each step links to the doc that covers that piece in full; skim
this once end to end, then come back to a step when you need it again.

| If you want... | Read |
|---|---|
| every `.env` variable, what breaks if it's wrong | [`CONFIGURATION.md`](CONFIGURATION.md) |
| the knowledge base in depth (sources, freshness, overrides, gaps) | [`KNOWLEDGE_BASE.md`](KNOWLEDGE_BASE.md) |
| backups, upgrades, the `agent-skills` submodule bump | [`OPERATIONS.md`](OPERATIONS.md) |
| Keycloak SSO instead of local email/password | [`KEYCLOAK.md`](KEYCLOAK.md) |
| a symptom you're staring at right now | [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) |

## 0. Prerequisites

- Docker Engine + Compose v2 (`docker compose version` works)
- A running, OpenAI-compatible LLM endpoint (base URL + API key) -- this is
  the vLLM/Qwen/etc. endpoint that powers chat. The knowledge base can reuse
  it, or use a different one.
- Jira and/or Confluence, if you want those as agent tools and/or as
  knowledge-base sources. Not required to bring the stack up.
- `openssl` on your `PATH` (used by `scripts/generate-secrets.sh`)

## 1. Clone

```bash
git clone --recurse-submodules <this-repo-url> agentflow
cd agentflow
```

`--recurse-submodules` matters: `agent-skills/` is a git submodule, and an
empty `agent-skills/` directory makes `docker compose up` fail building
`mcp-agent-skills`. If you forgot it:

```bash
git submodule update --init --recursive
```

## 2. Create `.env`

```bash
cp .env.example .env
chmod 600 .env
```

`.env` is gitignored -- every secret and every deployment-specific value
lives here, never in a tracked file.

## 3. Generate the secrets

```bash
scripts/generate-secrets.sh
```

**Expected output:**

```
# Paste these into .env (see .env.example for placement):

CREDS_KEY=b0c1...  (64 hex chars)
CREDS_IV=3f2a...   (32 hex chars)
JWT_SECRET=...
JWT_REFRESH_SECRET=...
MONGO_ROOT_PASSWORD=...
MONGO_APP_PASSWORD=...
MEILI_MASTER_KEY=...
POSTGRES_PASSWORD=...
KB_POSTGRES_PASSWORD=...
KB_READER_PASSWORD=...
OPENID_SESSION_SECRET=...  # only needed if using Keycloak
ADMIN_PASSWORD=...
ADMIN_PANEL_SESSION_SECRET=...

Back these up somewhere other than this host, separately from volume
backups -- see docs/OPERATIONS.md 'Secrets' section.
```

Paste each line into the matching variable in `.env`. The script refuses to
run a second time once `.env` already has `CREDS_KEY`/`JWT_SECRET` set --
that's deliberate, see [`OPERATIONS.md`](OPERATIONS.md) "Secrets": rotating
those on a live deployment makes stored credentials permanently unreadable.

## 4. Fill in the rest of `.env`

Everything below is a plain edit of `.env` -- no command to run. Full
reference for every variable: [`CONFIGURATION.md`](CONFIGURATION.md).

**Required for the stack to start at all:**

```bash
LIBRECHAT_IMAGE_TAG=v0.8.7        # a real published tag -- check upstream first
RAG_API_IMAGE_TAG=v0.9.0

VLLM_BASE_URL=https://your-llm-host/v1
VLLM_API_KEY=...                  # blank is fine if your endpoint needs none
VLLM_DEFAULT_MODEL=your-model-id  # the id GET {VLLM_BASE_URL}/models reports
MODEL_CONTEXT_TOKENS=262144       # the model's real context window, in tokens

RAG_EMBEDDINGS_MODEL=...          # an embeddings model your endpoint serves

ADMIN_EMAIL=admin@agentflow.local
ADMIN_NAME=Admin
ADMIN_USERNAME=admin
# ADMIN_PASSWORD was already filled in by step 3
```

**Optional, fill in if you want it from day one:**

```bash
# Jira/Confluence as agent tools (each user can instead supply their own
# credentials in the chat UI -- see README.md "Managing users")
MCP_TOOLSETS="jira confluence"
JIRA_BASE_URL=... JIRA_USERNAME=... JIRA_PASSWORD=...
CONFLUENCE_BASE_URL=... CONFLUENCE_USERNAME=... CONFLUENCE_PASSWORD=...
CONFLUENCE_DEPLOYMENT_TYPE=cloud   # required if confluence is in MCP_TOOLSETS

# Knowledge base -- see step 7 below; can be configured later, the stack
# comes up fine without it
```

If your LLM endpoint's DNS name doesn't resolve the way the containers need
(e.g. it's only reachable via a specific IP from inside Docker), also set
`MODEL_HOSTNAME`/`MODEL_HOST_IP` -- see `CONFIGURATION.md`.

## 5. Bring the stack up

```bash
make up
```

This is `scripts/bootstrap.sh`, and it's idempotent -- rerun it any time,
including after every later `.env` edit in this walkthrough.

**Expected output, roughly in this order:**

```
==> Generating searxng/settings.yml (gitignored) from its template...
==> Generating config/kb-sources.yaml (gitignored) from its template...
==> Rendering config/librechat.yaml from config/librechat.yaml.example...
    Done (recursionLimit=50, maxRecursionLimit=100, modelContextTokens=262144, summarizationRetainTokens=13107, defaultModel=your-model-id).
==> docker compose up -d --build
 [+] Running 11/11
  ✔ Network agentflow_backend      Created
  ✔ Network agentflow_frontend     Created
  ✔ Container agentflow-mongodb-1       Healthy
  ✔ Container agentflow-meilisearch-1   Healthy
  ...
==> Waiting for api to report healthy...
    api is healthy.
==> Checking for an existing user...
==> Creating admin account (admin@agentflow.local)...
    Created. Log in at the chat host with:
      email:    admin@agentflow.local
      password: <the ADMIN_PASSWORD from your .env>
==> Knowledge base...
    created config/kb-sources.yaml from the template (every source disabled)
    Applying 001_initial.sql...
    Applying 002_refresh_queue.sql...
    usable, but the catalog will stay empty until:
      - KB_SUMMARIZER_URL + KB_SUMMARIZER_MODEL (+ KB_SUMMARIZER_API_KEY if it needs one) -- without them entries are catalogued under their real titles but undescribed, and cross-language search will not work
      - a read-only service account: KB_CONFLUENCE_* / KB_JIRA_* / GITLAB_TOKEN -- sync has to see a space in order to catalog it
      - at least one source enabled in config/kb-sources.yaml -- run 'make kb-sources-discover WRITE=1' to see what is there
    then: make kb-sources-discover WRITE=1  ->  edit config/kb-sources.yaml  ->  make kb-sync SOURCE=<id>
==> Done. docker compose ps:
NAME                          STATUS
agentflow-api-1               Up (healthy)
agentflow-mongodb-1           Up (healthy)
agentflow-meilisearch-1       Up (healthy)
agentflow-vectordb-1          Up (healthy)
agentflow-rag_api-1           Up (healthy)
agentflow-mcp-agent-skills-1  Up (healthy)
agentflow-admin-panel-1       Up
agentflow-mcp-kb-1            Up (healthy)
agentflow-kb-db-1             Up (healthy)
agentflow-kb-scheduler-1      Up
agentflow-searxng-1           Up
```

The knowledge-base section is expected to say "usable, but... will stay
empty" on a fresh run -- that's step 7. It's not a failure.

If `api did not become healthy in time` instead, `docker compose logs api`
almost always shows why (usually a missing/invalid required `.env` value).

## 6. Log in

Open `http://localhost:${PORT:-3080}` and log in with the admin
email/password `make up` just printed. From here, Jira/Confluence tools
(if `MCP_TOOLSETS` is set) and the model picker should already work.

Create real accounts for your team the same idempotent way (never through
self-registration -- `ALLOW_REGISTRATION=false`):

```bash
make user-create EMAIL=a@b.com NAME="A B" USERNAME=ab
```

## 7. Fill the knowledge base (optional, but this is the part with the most steps)

Full depth: [`KNOWLEDGE_BASE.md`](KNOWLEDGE_BASE.md). Short version:

### 7a. Credentials

Add to `.env`:

```bash
KB_CONFLUENCE_BASE_URL=https://wiki.example.com   # falls back to CONFLUENCE_* if unset
KB_CONFLUENCE_USERNAME=kb-bot
KB_CONFLUENCE_PASSWORD=...                        # or KB_CONFLUENCE_PAT
KB_JIRA_BASE_URL=https://jira.example.com         # falls back to JIRA_*
KB_JIRA_PAT=...

KB_SUMMARIZER_URL=${VLLM_BASE_URL}                # or a different endpoint entirely
KB_SUMMARIZER_MODEL=your-model-id                 # must be one GET {url}/models actually lists
KB_SUMMARIZER_API_KEY=${VLLM_API_KEY}             # optional -- omit if the endpoint needs none
KB_SUMMARY_LANGUAGES=en                           # comma-separated, e.g. en,fa
```

```bash
make kb-setup   # picks up the new env vars without a full `make up`
```

### 7b. Confirm it's actually usable

```bash
make kb-check
```

**Expected output (everything ready):**

```json
{"ready": true, "checks": {
  "database": {"ok": true, "entries": {"live": 0, "soft_deleted": 0, "searchable": 0}, "migrations": ["001_initial.sql", "002_refresh_queue.sql"]},
  "summarizer": {"ok": true, "url": "https://your-llm-host/v1/models", "models": ["your-model-id"], "model": "your-model-id", "model_served": true, "structured_output": true, "authenticated": true},
  "sources": {"ok": false, "approved": true, "approved_from": "file", "enabled": [], "configured": [], "error": "no source is enabled -- nothing will be catalogued"}
}}
```

`ready: false` with only `sources` failing and `enabled: []` is expected
before step 7c -- nothing is catalogued yet because nothing is enabled.
`summarizer.ok: false` instead means the endpoint doesn't honor
`response_format`/the `system` role -- see `KNOWLEDGE_BASE.md` "When
something looks wrong" before assuming the model itself is incapable.

### 7c. Choose sources

```bash
make kb-sources-discover WRITE=1
```

**Expected output:**

```json
{"candidates": [{"id": "confluence-eng", "kind": "confluence", "found": 412}, {"id": "jira-product", "kind": "jira", "found": 38}], "file": "config/kb-sources.yaml", "added": 2, "already_configured": 0, "next": "edit config/kb-sources.yaml: set `enabled: true` on the sources you want"}
```

Edit `config/kb-sources.yaml`: everything discovery adds is
`enabled: false` by default. Flip the ones you want to `true`, delete the
rest.

### 7d. First sync

One source, to see it work:

```bash
make kb-sync SOURCE=confluence-eng DRY_RUN=1   # what it would read, writes nothing
make kb-sync SOURCE=confluence-eng             # for real
```

**Expected output (a real run):**

```json
{"source_id": "confluence-eng", "mode": "full", "dry_run": false, "force": false, "seen": 412, "created": 412, "updated": 0, "unchanged": 0, "revived": 0, "missing": 0, "summarized": 412, "rejected": [], "checkpoint": "2026-09-29T12:00:00Z"}
```

Watch its progress live: `kb sync` prints `<source>: syncing #N -- <title>`
to stderr as it goes, overwriting the same line.

Once you've enabled several sources, sync all of them in one go instead of
one `make kb-sync SOURCE=...` at a time:

```bash
make kb-sync-all           # add FORCE=1 to bypass the content-hash gate
```

It reads every `enabled: true` id from `config/kb-sources.yaml` and syncs
each one in turn, same live progress per source. (Note: after the very
first `make up`/`make kb-setup` with sources already enabled and the
catalog still empty, this now also runs automatically as part of bootstrap
-- see step 5's output. Run it by hand any time after that; an unchanged
sync costs nothing, so rerunning is always safe.)

### 7e. Check what's in the catalog

```bash
make kb-status
```

**Expected output:**

```json
{"entries": {"live": 412, "soft_deleted": 0, "searchable": 412}, "by_source": [{"source_id": "confluence-eng", "entries": 412}], "by_type": [{"type": "doc", "entries": 412}], "overrides": 0, "gaps": 0, "migrations": ["001_initial.sql", "002_refresh_queue.sql"], "checkpoints": [{"source_id": "confluence-eng", "checkpoint": "2026-09-29T12:00:00Z"}]}
```

From here, leave it: `kb-scheduler` re-syncs on the cadences set in
`config/kb-sources.yaml` (15-minute incremental by default). No cron, no
manual re-running.

### 7f. Let the agents actually use it

```bash
make render-config && make restart SERVICE=api   # api picks up mcp-kb
make agent-import DRY_RUN=1                       # preview
make agent-import                                 # apply
```

**Expected output (`agent-import`):**

```
Importing agents/front-door.yaml...
  front-door: created (tools: kb_search, kb_get, jira_search, ...)
1 agent imported, 0 skipped, 0 errors.
```

Ask the chat something your Confluence/Jira actually covers -- the agent
should search the catalog first, cite what it used, and say so plainly if
the catalog has nothing.

## 8. Day to day, once all of the above is done once

```bash
make ps                              # health of all services
make logs SERVICE=api                # or any other service name
make kb-status                       # what the catalog holds
make kb-sync-all                     # re-sync everything enabled, on demand
make backup                          # snapshot every volume + .env
```

`make help` lists every target; `Makefile` documents nothing that isn't
also in `README.md` or one of the docs above.
