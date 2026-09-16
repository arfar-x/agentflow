# Troubleshooting

## MCP provider registers fine, but every tool call fails

This is the single most common failure mode in this stack -- see
`AGENTS.md` "Architecture" for why it exists at all. Symptom: **Studio ->
Tools -> MCP -> Agent Skills (Jira & Confluence)** shows the tool list
just fine (`jira_*`, `confluence_*`, `doc_gen`, `get_skill`,
`list_skills`), registration via `scripts/bootstrap.sh` reported success,
but invoking any tool from a chat/app fails or times out.

1. Check the proxy first, not the MCP server:
   ```bash
   docker compose logs ssrf_proxy | grep -i denied
   ```
   A `TCP_DENIED` line naming `mcp-agent-skills` or `searxng` means
   `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS` in `.env` doesn't include that
   hostname. `api`/`worker` route *every* outbound tool/MCP/HTTP-Request
   call through this proxy, which denies private-network destinations
   (which `mcp-agent-skills` and `searxng` both are, being internal-only
   compose services) unless explicitly allowlisted.
2. Fix: confirm `.env` has
   `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=mcp-agent-skills,searxng` (comma-
   separated, no spaces), then
   ```bash
   docker compose up -d ssrf_proxy
   ```
   Env var changes need a container recreate, not just a restart of the
   process inside it.
3. If the proxy logs show no denial, check `mcp-agent-skills` itself
   next (see "Tools missing" section below for the direct MCP probe
   command) -- at that point the problem is in `agent-skills`' own code
   or credentials, not in the proxy/registration layer.

## Tools missing, or fewer than expected, from Studio's MCP tool list

1. `docker compose logs mcp-agent-skills` -- introspection failures for
   one toolset are logged as warnings and **skip only that toolset's
   tools**; `get_skill`/`list_skills` keep working regardless. Look for
   `IntrospectionError` and the toolset name it names.
2. Most common cause: a toolset is in `MCP_TOOLSETS` but its
   `requirements.txt` never got installed into the image. Rebuild:
   ```bash
   docker compose up -d --build mcp-agent-skills
   ```
   Then refresh Dify's cached tool list -- **Studio -> Tools -> MCP ->
   Agent Skills (Jira & Confluence) -> refresh** (the reload icon on that
   provider's card). Registering a provider does not re-poll its tool
   list on every call; a rebuild alone won't update what Studio shows.
3. Confirm the tool list directly, bypassing Studio entirely:
   ```bash
   docker compose exec api sh -c "curl -s http://mcp-agent-skills:8321/mcp -X POST \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2026-03-26\",\"capabilities\":{},\"clientInfo\":{\"name\":\"debug\",\"version\":\"0\"}}}'"
   ```
   A `200` with a JSON-RPC result means the MCP server itself is fine
   from inside the compose network, and the problem is specifically in
   Dify's registration/cache, not in `agent-skills`.

## `{"error": {"type": "missing_environment_variables", ...}}`

A `jira_*` (or other toolset) call returns this instead of doing
anything. The named variables aren't set in `mcp-agent-skills`'s own
environment -- check `.env` has them
(`JIRA_BASE_URL`/`JIRA_USERNAME`/`JIRA_PASSWORD`, or the `CONFLUENCE_*`
equivalents), then:

```bash
docker compose up -d mcp-agent-skills
```

Unlike the per-user Valves design in this project's Open WebUI branch,
there is no per-user setup-hint flow here -- credentials are shared and
configured once in `.env` (see `docs/CONFIGURATION.md` "Per-user vs.
shared credentials"), so this error always means an operator needs to fix
`.env`, never an end user filling in a form.

## A tool call hangs, then times out

`mcp-server/lib/execute.py` (in the `agent-skills` submodule) caps each
CLI subprocess at 60 seconds. `scripts/bootstrap.sh` registers the MCP
provider with `timeout: 90, sse_read_timeout: 90` in its `configuration`
specifically so Dify's own client-side timeout never fires first. If
you're still seeing timeouts:

- `docker compose logs mcp-agent-skills` for what the subprocess was
  doing (e.g. Jira itself being slow).
- Confirm nothing downstream (a corporate proxy, Jira/Confluence's own
  network) is the actual slow leg -- the 60s cap is per CLI subprocess
  call, not per conversation turn.
- If you need a longer window, re-run the MCP registration step in
  `scripts/bootstrap.sh` after raising both timeout values, or edit the
  provider's configuration directly in Studio.

## `mcp-agent-skills` container exits immediately on startup

Almost always `AGENT_SKILLS_REPO_ROOT` pointing at a directory with no
`skills/` child -- `mcp-server/lib/registry.py` has no existence guard
and crashes at import, before the server ever starts listening. This
shouldn't happen with this stack's Dockerfile (it sets
`AGENT_SKILLS_REPO_ROOT=/opt/repo` and copies `skills/` there itself),
but would show up this way if the Dockerfile is ever edited to change
that layout.

## Cloned the repo and the `agent-skills/` directory is empty

You forgot `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

`docker compose build mcp-agent-skills` will otherwise fail with a
missing `mcp-server/requirements.txt` or similar, since the build context
(`./agent-skills`) is empty.

## `scripts/bootstrap.sh` fails at the login/CSRF step

Dify's console login isn't a bearer-token API -- `bootstrap.sh`
authenticates the same way Dify's own frontend does: `POST
/console/api/login` with a base64-encoded password (Dify's own
`@decrypt_password_field` decorator expects this, it is not real
encryption, just an API contract), storing the response's `access_token`/
`refresh_token`/`csrf_token` cookies in a cookie jar, then sending that
`csrf_token` back as an `X-CSRF-Token` header on every subsequent
authenticated call (double-submit pattern). If this step fails:

- Confirm `DIFY_ADMIN_EMAIL`/`DIFY_ADMIN_PASSWORD` in `.env` actually
  match what `/console/api/setup` created -- if you changed the password
  in Studio after first setup, `.env` is now stale and `bootstrap.sh`'s
  login will 401 on every rerun.
- A `finished: true`/already-set-up response from `/console/api/setup`
  is expected and not an error on any run after the first -- the script
  treats it as "skip account creation, proceed to login."
- If login itself 401s and the password truly matches, check
  `docker compose logs api` for the actual rejection reason rather than
  guessing from the script's own output.

## `scripts/bootstrap.sh` times out waiting for `api` to be healthy

Check `docker compose logs api` directly -- the script only polls
`docker compose ps`'s health status, so whatever's actually failing
(Postgres auth, a bad `PGVECTOR_*` value, plugin_daemon unreachable) will
be in those logs, not in the script's own output. A timeout means
something is genuinely stuck, not just a slow-but-working boot.

## Model provider shows "not installed" and `bootstrap.sh` skips model config

Expected on a fresh deployment -- see `docs/CONFIGURATION.md` "LLM
endpoint (vLLM)" for why plugin installation isn't automated here.
Install it once (**Studio -> Plugins -> Marketplace -> search
"OpenAI-API-compatible" -> Install**), then rerun `scripts/bootstrap.sh`
(or `make up`) to configure your vLLM endpoint declaratively against it.

## Knowledge base / RAG indexing fails outright

Almost always a `PGVECTOR_*` mismatch -- these have no built-in default
in Dify's code (see `docs/CONFIGURATION.md` "Vector store (RAG)"), and a
wrong `PGVECTOR_PORT` in particular (the Python default is `5433`; this
compose file's `pgvector` service listens on the standard `5432`) fails
silently at index time rather than at startup. Check
`docker compose logs worker` for the actual connection error, confirm
`PGVECTOR_HOST`/`PGVECTOR_PORT`/`PGVECTOR_USER`/`PGVECTOR_PASSWORD`/
`PGVECTOR_DATABASE` in `.env` match the `pgvector` service's own
`PGVECTOR_PGUSER`/`PGVECTOR_POSTGRES_PASSWORD`/`PGVECTOR_POSTGRES_DB`,
then `docker compose up -d api worker`.

## `api`/`worker` fail Postgres auth after changing `DB_PASSWORD`

Postgres's own init scripts only run once, against an empty data
directory -- exactly like `PGVECTOR_POSTGRES_PASSWORD` and every other
first-boot-only credential in this stack. Editing `DB_PASSWORD` in `.env`
after `./volumes/db/data` already has data changes nothing inside
Postgres; the old password is still what's actually stored there. Either
update it inside Postgres directly
(`ALTER USER ... WITH PASSWORD ...`, then update `.env` to match) or, on
a deployment with nothing worth keeping yet, wipe and reinit:

```bash
docker compose down
rm -rf ./volumes/db/data
scripts/bootstrap.sh
```

**Never run `rm -rf ./volumes` wholesale as a fix for this** -- that also
deletes `pgvector` data, uploaded files, and installed plugins, none of
which this specific failure has anything to do with. Delete only the
narrowest path that actually holds the stale credential.
