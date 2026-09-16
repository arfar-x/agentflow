# Operations

## First run

```bash
git clone --recurse-submodules <this-repo-url> agentflow
cd agentflow
cp .env.example .env
scripts/generate-secrets.sh          # paste output into .env
chmod 600 .env
# fill in the rest of .env: VLLM_BASE_URL/VLLM_API_KEY/VLLM_MODEL_NAME,
# JIRA_*/CONFLUENCE_*, DIFY_ADMIN_EMAIL -- see docs/CONFIGURATION.md
scripts/bootstrap.sh
```

`scripts/bootstrap.sh` (also `make up`/`make bootstrap`) is the whole
first-run sequence in one idempotent command:

1. Generates `searxng/settings.yml` (gitignored) from its template with a
   fresh random `secret_key` if it doesn't exist yet.
2. `docker compose up -d --build`, then waits for `api` to actually report
   healthy.
3. Creates the admin account via Dify's own unauthenticated
   `/console/api/setup` endpoint, from `DIFY_ADMIN_EMAIL`/
   `DIFY_ADMIN_NAME`/`DIFY_ADMIN_PASSWORD` -- skipped automatically if any
   account already exists (`/console/api/setup` reports `finished`), so
   rerunning after a restart or upgrade does nothing destructive.
4. Logs in as that admin (cookie + CSRF token, the same flow Dify's own
   frontend uses) and registers `mcp-agent-skills` as a Dify MCP tool
   provider via `POST /console/api/workspaces/current/tool-provider/mcp`
   -- skipped if a provider with that `server_identifier` already exists.
5. If the OpenAI-API-compatible model plugin is installed, configures
   `VLLM_BASE_URL`/`VLLM_API_KEY`/`VLLM_MODEL_NAME` as a custom model
   credential declaratively. If it isn't installed yet, prints the
   one-time manual Studio step and stops there without failing the rest
   of the run -- see `docs/CONFIGURATION.md` "LLM endpoint (vLLM)".

`searxng/settings.yml` follows the same "secret never committed" rule as
`.env`: only `searxng/settings.yml.example` (a placeholder `secret_key`)
is tracked in git; the real file is gitignored and generated locally.

Two things this stack does *not* automate, on purpose, because there's no
verified declarative or safe-to-guess way to do them (see
`docs/CONFIGURATION.md` and `AGENTS.md` "Architecture" for the reasoning
behind each):

- **Installing the OpenAI-API-compatible model plugin itself** -- a
  one-time Studio click (Plugins -> Marketplace -> search -> Install).
- **Creating and publishing the actual Chatflow/Agent app** that uses the
  configured model and MCP tools -- a one-time Studio step, 2-3 minutes,
  documented in README.md "Using the Jira/Confluence tools".

## Volumes -- what each one is worth

Everything durable is a **bind mount under `./volumes/`** (Dify's own
convention in its official compose file, kept as-is here rather than
converted to named Docker volumes -- this is what a straight `docker
compose down -v` would otherwise wipe without an extra confirmation step,
so treat `./volumes/` with the same care you'd give any other stateful
directory on the host).

| Path under `./volumes/` | Contents | Losing it means |
|---|---|---|
| `db/data` | `db_postgres` -- Dify's own app database: users, workspaces, apps, conversations, tool/model configuration | Total loss. This is the directory that matters most. |
| `pgvector/data` | the separate `pgvector` Postgres instance -- RAG/knowledge-base embeddings | Re-upload and re-index affected datasets. |
| `redis/data` | Celery broker/backend + cache | Rebuildable; in-flight async jobs (indexing, scheduled runs) are lost. |
| `app/storage` | `api`/`worker`'s own file storage -- uploaded files, generated files, plugin working data not covered by `plugin_daemon` below | Those files are gone. |
| `plugin_daemon` | installed plugin packages (including the OpenAI-API-compatible model plugin once installed) and their persistent state | Re-install plugins from the Marketplace/GitHub after restore. |
| `sandbox/dependencies` | cached Python packages the Code tool's sandbox installs on demand | Rebuildable; first Code-tool run after a wipe is slower, nothing else. |
| `agent_local_sandbox_home` / `agent_local_sandbox_workspace` | Agent Soul (`agent_backend`)'s own local sandbox state | Rebuildable, same as above. |

## Secrets

`SECRET_KEY` encrypts everything Dify stores in Postgres -- every model
provider API key and every tool/MCP credential (including the shared
`JIRA_*`/`CONFLUENCE_*` values once registered). **Restoring
`./volumes/db/data` alongside a *different* `SECRET_KEY` produces a
database that opens normally but whose stored credentials cannot be
decrypted, ever.** There is no re-encryption step to recover from this.

The same "must match its pair" rule from `docs/CONFIGURATION.md` applies
to `REDIS_PASSWORD`/`CELERY_BROKER_URL`, `PGVECTOR_PASSWORD`/
`PGVECTOR_POSTGRES_PASSWORD`, and `SANDBOX_API_KEY`/`CODE_EXECUTION_API_KEY`
-- restoring `.env` from a different point in time than `./volumes/` risks
splitting one of these pairs across containers that no longer agree,
which shows up as an auth failure inside the stack (Celery unable to
reach Redis, `api` unable to reach `pgvector` or `sandbox`), not as a
Dify-level error message.

Rules that follow from that:

- Back up `.env` **every time** you back up `./volumes/`, as one unit --
  `scripts/backup.sh` does this automatically.
- Never run `scripts/generate-secrets.sh` again on a host that already has
  data in `./volumes/` -- it refuses to run if `.env` already has secrets
  set, but a manual hand-edit of `SECRET_KEY` or any of the paired values
  bypasses that guard.
- Store at least one copy of `.env` somewhere other than this host.

## Backup / restore

```bash
scripts/backup.sh                      # -> backups/<timestamp>/
scripts/backup.sh /mnt/nas/agentflow   # or any other destination
```

Restoring:

```bash
scripts/restore.sh backups/<timestamp>   # stops the stack, then restores ./volumes/
# restore.sh does NOT touch .env -- if the backup's .env differs from your
# current one, especially SECRET_KEY or any of the three paired values
# above, copy it into place yourself BEFORE the next step, or Postgres's
# stored credentials will not decrypt
docker compose up -d
```

Run this pair once, deliberately, against a disposable copy before you
actually need it in an emergency -- confirming a backup script works is
the only way to know it works.

## Upgrades

1. Pick the new Dify version tag and re-diff this repo's
   `docker-compose.yml` against a fresh copy of upstream's
   `docker/docker-compose.yaml` at that tag -- this file is Dify's own
   compose, hand-trimmed (see `AGENTS.md` "Architecture"), so a version
   bump means re-applying the same trim to the new file, not editing image
   tags in place. Watch specifically for new services, renamed env vars,
   and changes to `ssrf_proxy`'s allowlist mechanism.
2. `scripts/backup.sh` first, always.
3. Update `.env` for any new/renamed variables the diff surfaced, then
   `docker compose pull && docker compose up -d --build`.
4. Watch `docker compose logs -f api` through startup; confirm Studio
   loads, an existing app is still visible, and the `mcp-agent-skills`
   tool provider still shows its tool list before considering the upgrade
   done.

## Updating the `agent-skills` submodule

Skill changes live in the `agent-skills` GitHub repo, not in this one.
Pulling in a new skill, a fixed toolset, or a version bump means moving
the submodule's pinned commit:

```bash
cd agent-skills
git fetch --tags
git checkout <new-tag-or-commit>       # e.g. git checkout v0.9.0
cd ..
git add agent-skills
git commit -m "chore: bump agent-skills to v0.9.0"
docker compose up -d --build mcp-agent-skills
```

Confirm the new tool list after rebuilding (`docs/TROUBLESHOOTING.md` has
the exact command) -- especially after adding a toolset to
`MCP_TOOLSETS`, since a missing `requirements.txt` install shows up as
tools silently missing from Studio's MCP tool list, not as a build
failure. Dify caches a registered MCP provider's tool list at
registration/refresh time, not per-call -- after a rebuild that changes
the available tools, go to **Studio -> Tools -> MCP -> Agent Skills
(Jira & Confluence) -> refresh** (the reload icon on that provider's
card) so Dify re-fetches the list rather than serving a stale one.

## Logs and health

```bash
docker compose ps                              # health status of all services
docker compose logs -f api                     # Dify console/API logs
docker compose logs -f worker                  # async indexing/job logs
docker compose logs -f mcp-agent-skills        # MCP server logs, incl. per-toolset introspection warnings
docker compose logs -f ssrf_proxy              # squid access/deny logs -- check here first for silent tool-call failures
```

## Restart / recovery ordering

`depends_on: condition: service_healthy` in `docker-compose.yml` already
enforces the right order on a normal `docker compose up -d` -- `api` will
not start against a cold `db_postgres`/`redis` or an unready
`plugin_daemon`. If something is stuck, bring the dependency-only
services up first and confirm they're healthy before touching `api`/
`worker`:

```bash
docker compose up -d db_postgres redis pgvector sandbox plugin_daemon mcp-agent-skills ssrf_proxy
docker compose ps        # wait for "healthy" on all of the above
docker compose up -d api worker worker_beat web nginx
```
