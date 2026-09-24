# Operations

## First run

```bash
git clone --recurse-submodules <this-repo-url> agentflow
cd agentflow
cp .env.example .env
scripts/generate-secrets.sh          # paste output into .env
chmod 600 .env
# fill in the rest of .env: image tags, VLLM_BASE_URL/VLLM_API_KEY,
# JIRA_*, ADMIN_EMAIL/ADMIN_USERNAME -- see docs/CONFIGURATION.md
scripts/bootstrap.sh
```

`scripts/bootstrap.sh` is the whole first-run sequence in one idempotent
command: generate `searxng/settings.yml` (gitignored) from its template
with a fresh random `secret_key` if it doesn't exist yet, `docker compose
up -d --build`, wait for `api` to actually report healthy, then create the
admin account (from `ADMIN_EMAIL`/`ADMIN_NAME`/`ADMIN_USERNAME`/
`ADMIN_PASSWORD` in `.env`) via LibreChat's own `config/create-user.js` --
the same officially-shipped tool a human would run by hand, just automated
and safe to rerun. It checks for an existing user first and skips creation
if one is already there, so running it again after a restart or an
upgrade does nothing destructive.

`searxng/settings.yml` follows the same "secret never committed" rule as
`.env`: only `searxng/settings.yml.example` (a placeholder `secret_key`)
is tracked in git; the real file is gitignored and generated locally, same
as `.env` itself.

Two things it does *not* do, because they're already handled elsewhere,
declaratively, with no script needed:

- **The LibreChat app-level Mongo user** is created by
  `mongo-init/init-librechat-user.sh`, mounted into
  `mongodb`'s `/docker-entrypoint-initdb.d/` -- the official mongo image's
  own convention for running init scripts, with the container's env
  already available, exactly once, only when `mongodb_data` is being
  initialized for the first time. It creates the user in `admin`
  (matching `authSource=admin` in `MONGO_URI`), not in `LibreChat` --
  Mongo looks up a user's credentials in whichever database `authSource`
  names, regardless of which database the granted role applies to;
  getting this backwards produces
  `UserNotFound: Could not find user "..." for db "admin"` on every
  connection attempt, LibreChat included.
- **The Mongo root user** is created by the image's own
  `MONGO_INITDB_ROOT_USERNAME`/`MONGO_INITDB_ROOT_PASSWORD` handling,
  same as any mongo image -- nothing custom here at all.

Neither of these fires again on an existing volume, so restoring a backup
or restarting the stack never re-runs them -- see "Backup / restore" below
for what that means for rotating the underlying passwords.

## Volumes -- what each one is worth

| Volume | Contents | Losing it means |
|---|---|---|
| `mongodb_data` | users, conversations, **agent definitions**, MCP tool state | Total loss. This is the volume that matters most. |
| `pgvector_data` | RAG embeddings for user-uploaded files | Re-upload and re-embed affected files. |
| `kb_data` | The knowledge catalog: entries, overrides, sync checkpoints, gap log | The catalog and its search index rebuild from the sources (`make kb-sync`, once phase 6 lands); the **overrides, checkpoints and gap log do not** -- those are the reason this volume is backed up. |
| `meili_data` | search index | Rebuildable from Mongo; not backup-critical but included for convenience. |
| `librechat_uploads` | user-uploaded files | Those files are gone. |
| `librechat_images` | generated/attached images | Those images are gone. |
| `librechat_logs` | application logs | Logs only. |

## Secrets

`CREDS_KEY`, `CREDS_IV`, `JWT_SECRET`, `JWT_REFRESH_SECRET` encrypt
everything LibreChat stores in Mongo (API keys, MCP credentials, etc).
**Restoring `mongodb_data` alongside a *different* set of these four values
produces a database that opens normally but whose stored credentials cannot
be decrypted, ever.** There is no re-encryption step to recover from this.

Rules that follow from that:

- Back up `.env` **every time** you back up the volumes, as one unit.
  `scripts/backup.sh` does this automatically.
- Never run `scripts/generate-secrets.sh` again on a host that already has
  data in `mongodb_data` -- it refuses to run if `.env` already has secrets
  set, but a manual hand-edit of those four values bypasses that guard.
- Store at least one copy of `.env` somewhere other than this host.

## Backup / restore

```bash
scripts/backup.sh                    # -> backups/<timestamp>/
scripts/backup.sh /mnt/nas/agentflow # or any other destination
```

Restoring:

```bash
scripts/restore.sh backups/<timestamp>
# then, if the backup's .env differs from your current one -- especially
# the four secrets above -- copy it into place BEFORE starting the stack
docker compose up -d
```

Run this pair once, deliberately, against a disposable copy before you
actually need it in an emergency -- confirming a backup script works is the
only way to know it works.

## Knowledge base migrations

Schema changes live in `kb/migrations/` and are applied by `make kb-init`, which
`make up` runs for you. They are append-only -- a shipped file is never edited --
so an upgrade applies what is new and skips what is already recorded in
`schema_migration`. Rerunning is a no-op.

Restart order after an upgrade: `kb-db` first, then `mcp-kb` and
`kb-scheduler` (both reconnect on their own, so `docker compose up -d` in any
order works; the ordering only matters if you stop the database deliberately).
The scheduler survives a database blip -- the tick fails, is logged, and the
next one runs.

## Upgrades

1. Pick the new `LIBRECHAT_IMAGE_TAG` / `RAG_API_IMAGE_TAG`.
2. `scripts/backup.sh` first, always.
3. Update `.env`, then `docker compose pull && docker compose up -d`.
4. Watch `docker compose logs -f api` through startup; confirm the chat UI
   loads and an existing conversation is still visible before considering
   the upgrade done.

## Updating the `agent-skills` submodule

Skill changes live in the `agent-skills` GitHub repo, not in this one.
Pulling in a new skill, a fixed toolset, or a version bump means moving the
submodule's pinned commit:

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
the exact command) -- especially after adding a toolset to `MCP_TOOLSETS`,
since a missing `requirements.txt` install shows up as tools silently
missing from the picker, not as a build failure.

Note `doc_gen`'s enum of document types (`prd`, `trd`, `adr`, `rfc`, ...) is
built once at container startup. A new document-generation skill in
`agent-skills` always needs `docker compose up -d --build mcp-agent-skills`,
never just a config reload.

## Logs and health

```bash
docker compose ps                              # health status of all six services
docker compose logs -f api                     # LibreChat app logs
docker compose logs -f mcp-agent-skills         # MCP server logs, incl. per-toolset introspection warnings
```

## Restart / recovery ordering

`depends_on: condition: service_healthy` in `docker-compose.yml` already
enforces the right order on a normal `docker compose up -d` -- `api` will
not start against a cold `mongodb` or an unreachable `mcp-agent-skills`. If
something is stuck, bring the dependency-only services up first and confirm
they're healthy before touching `api`:

```bash
docker compose up -d mongodb meilisearch vectordb mcp-agent-skills
docker compose ps        # wait for "healthy" on all four
docker compose up -d rag_api api
```
