# Operations

## First run

```bash
git clone --recurse-submodules <this-repo-url> agentflow
cd agentflow
cp .env.example .env
scripts/generate-secrets.sh          # paste output into .env
chmod 600 .env
# fill in the rest of .env: image tag, VLLM_BASE_URL/VLLM_API_KEY,
# JIRA_*/CONFLUENCE_*, WEBUI_ADMIN_EMAIL -- see docs/CONFIGURATION.md
scripts/bootstrap.sh
```

`scripts/bootstrap.sh` is the whole first-run sequence in one idempotent
command: generate `searxng/settings.yml` (gitignored) from its template
with a fresh random `secret_key` if it doesn't exist yet, `docker compose
up -d --build`, wait for `open-webui` to actually report healthy (Open
WebUI creates the admin account itself on startup, from
`WEBUI_ADMIN_EMAIL`/`WEBUI_ADMIN_NAME`/`WEBUI_ADMIN_PASSWORD` in `.env`,
if no user exists yet -- its own first-party headless-bootstrap
mechanism, safe to leave those variables set permanently since it no-ops
once any user exists), then sign in as that admin and push
`config/tools/agent_skills.py` into Open WebUI as a Tool every signed-in
user can use. Running it again after a restart or an upgrade only
re-syncs the Tool's content -- never destructive.

`searxng/settings.yml` follows the same "secret never committed" rule as
`.env`: only `searxng/settings.yml.example` (a placeholder `secret_key`)
is tracked in git; the real file is gitignored and generated locally, same
as `.env` itself.

One thing it does *not* do, because Postgres's own official image already
handles it declaratively, with no script needed: **the Postgres role and
database** are created by the image's own `POSTGRES_USER`/
`POSTGRES_PASSWORD`/`POSTGRES_DB` handling on first init, the same way
any Postgres image works -- nothing custom here. That doesn't fire again
on an existing volume, so restoring a backup or restarting the stack never
re-runs it -- see "Backup / restore" below for what that means for
rotating the underlying password.

## Volumes -- what each one is worth

| Volume | Contents | Losing it means |
|---|---|---|
| `postgres_data` | users, chats, **Tool definitions and every user's own Valves (their Jira/Confluence credentials)**, RAG embeddings for uploaded files | Total loss. This is the volume that matters most. |
| `open_webui_data` | user-uploaded files, generated/cached assets, application logs | Those files/logs are gone; conversation text itself lives in Postgres, not here. |

## Secrets

`WEBUI_SECRET_KEY` signs session tokens and encrypts everything Open
WebUI stores in Postgres (each user's Jira/Confluence Valves included).
**Restoring `postgres_data` alongside a *different* `WEBUI_SECRET_KEY`
produces a database that opens normally but whose stored credentials
cannot be decrypted, ever.** There is no re-encryption step to recover
from this.

Rules that follow from that:

- Back up `.env` **every time** you back up the volumes, as one unit.
  `scripts/backup.sh` does this automatically.
- Never run `scripts/generate-secrets.sh` again on a host that already has
  data in `postgres_data` -- it refuses to run if `.env` already has a
  secret set, but a manual hand-edit of that value bypasses that guard.
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
# WEBUI_SECRET_KEY -- copy it into place BEFORE starting the stack
docker compose up -d
```

Run this pair once, deliberately, against a disposable copy before you
actually need it in an emergency -- confirming a backup script works is the
only way to know it works.

## Upgrades

1. Pick the new `OPEN_WEBUI_IMAGE_TAG`.
2. `scripts/backup.sh` first, always.
3. Update `.env`, then `docker compose pull && docker compose up -d`.
4. Watch `docker compose logs -f open-webui` through startup; confirm the
   chat UI loads and an existing conversation is still visible before
   considering the upgrade done.
5. Confirm `config/tools/agent_skills.py` still loads without error
   (Workspace -> Tools -> open it; a red error banner instead of the
   Valves form means something in the Tool framework's API changed --
   see this file's own top-of-file comment about the `mcp` package
   import it relies on).

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

**Unlike LibreChat's raw MCP connection, `config/tools/agent_skills.py`
does not auto-discover agent-skills' tool list.** If the new version adds
a Jira/Confluence action (a new `jira_*`/`confluence_*` subcommand), add
one corresponding method to that file in the same commit -- see its own
top-of-file comment for why (Open WebUI's Tool-spec builder needs a real
Python method per callable, not a schema fetched live per request) and
copy the pattern of an existing method with the same read/write shape.
Confirm the new tool list after rebuilding (`docs/TROUBLESHOOTING.md` has
the exact command) -- especially after adding a toolset to `MCP_TOOLSETS`,
since a missing `requirements.txt` install shows up as tools silently
missing, not as a build failure.

Note `doc_gen`'s available `doc_type` values are whatever skills in this
version declare `metadata.doc_type` -- `config/tools/agent_skills.py`
doesn't hardcode the list (it changes as `agent-skills` adds document
types), so it tells the model to call `list_skills` first instead.

## Logs and health

```bash
docker compose ps                              # health status of all four services
docker compose logs -f open-webui              # Open WebUI app logs
docker compose logs -f mcp-agent-skills         # MCP server logs, incl. per-toolset introspection warnings
```

## Restart / recovery ordering

`depends_on: condition: service_healthy` in `docker-compose.yml` already
enforces the right order on a normal `docker compose up -d` -- `open-webui`
will not start against a cold `postgres` or an unreachable
`mcp-agent-skills`. If something is stuck, bring the dependency-only
services up first and confirm they're healthy before touching
`open-webui`:

```bash
docker compose up -d postgres mcp-agent-skills
docker compose ps        # wait for "healthy" on both
docker compose up -d open-webui
```
