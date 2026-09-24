# Troubleshooting

## MCP tools missing from the chat tool picker

`agent-skills` tools (`doc_gen`, `jira_*`, ...) not showing up, or fewer
than expected:

1. `docker compose logs mcp-agent-skills` -- introspection failures for one
   toolset are logged as warnings and **skip only that toolset's tools**;
   `get_skill`/`list_skills` keep working regardless. Look for
   `IntrospectionError` and the toolset name it names.
2. Most common cause: a toolset is in `MCP_TOOLSETS` but its
   `requirements.txt` never got installed into the image. Rebuild:
   ```bash
   docker compose up -d --build mcp-agent-skills
   ```
3. Confirm the tool list directly, bypassing the chat UI:
   ```bash
   docker compose exec api sh -c "curl -s http://mcp-agent-skills:8321/mcp -X POST \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2026-03-26\",\"capabilities\":{},\"clientInfo\":{\"name\":\"debug\",\"version\":\"0\"}}}'"
   ```
   A `200` with a JSON-RPC result means the MCP server itself is fine and
   the problem is in LibreChat's `mcpServers` config or its own connection
   to it, not in `agent-skills`.

## `{"error": {"type": "missing_environment_variables", ...}}`

A `jira_*` (or other toolset) call returns this instead of doing anything.
The named variables aren't set in `mcp-agent-skills`'s environment -- check
`.env` has them, then `docker compose up -d mcp-agent-skills` to pick up the
change (env var changes need a container recreate, not just a restart of
the process inside it).

## A tool call hangs, then times out

`mcp-server/lib/execute.py` caps each CLI subprocess at 60 seconds.
`config/librechat.yaml` sets the MCP client timeout to 90 seconds
specifically so it never fires first. If you're still seeing timeouts:

- Check `docker compose logs mcp-agent-skills` for what the subprocess was
  doing (e.g. Jira itself being slow).
- Confirm nothing downstream (a corporate proxy, Jira/Confluence's own
  network) is the actual slow leg -- the 60s cap is per CLI subprocess call,
  not per conversation turn.

## `mcp-agent-skills` container exits immediately on startup

Almost always `AGENT_SKILLS_REPO_ROOT` pointing at a directory with no
`skills/` child -- `mcp-server/lib/registry.py` has no existence guard and
crashes at import, before the server ever starts listening. This shouldn't
happen with this stack's Dockerfile (it sets `AGENT_SKILLS_REPO_ROOT=/opt/repo`
and copies `skills/` there itself), but would show up this way if the
Dockerfile is ever edited to change that layout.

## Cloned the repo and the `agent-skills/` directory is empty

You forgot `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

`docker compose build mcp-agent-skills` will otherwise fail with a missing
`mcp-server/requirements.txt` or similar, since the build context
(`./agent-skills`) is empty.

## `api` still fails Mongo auth after changing `MONGO_APP_PASSWORD`

`mongo-init/init-librechat-user.sh` only runs once, when `mongodb_data` is
first initialized -- exactly like `MONGO_INITDB_ROOT_USERNAME`/
`MONGO_INITDB_ROOT_PASSWORD` themselves. Editing `MONGO_APP_PASSWORD` in
`.env` on an already-initialized volume changes nothing inside Mongo; the
old password is still what's actually stored. Either update it inside
Mongo directly (`db.getSiblingDB("admin").updateUser(...)`) or, on a
deployment with nothing worth keeping yet, wipe and reinit:

```bash
docker compose down
docker volume rm agentflow_mongodb_data
scripts/bootstrap.sh
```

## `scripts/bootstrap.sh` times out waiting for `api` to be healthy

Check `docker compose logs api` directly -- the script only polls
`docker compose ps`'s health status, so whatever's actually failing
(Mongo auth, the MCP domain allowlist, a bad `VLLM_BASE_URL`) will be in
those logs, not in the script's own output. The healthcheck's
`start_period` is 60s and the script polls for up to 5 minutes, so a
slow-but-working boot won't false-positive here -- a timeout means
something is genuinely stuck.

## OIDC login redirects to Keycloak but comes back with an error

- **redirect_uri mismatch**: the URI Keycloak sees on the way back must
  exactly match a **Valid redirect URI** on the client, including scheme
  and trailing path -- `https://<host>/oauth/openid/callback`, not
  `https://<host>/oauth/openid/callback/` or a bare `http://`.
- **Access refused after a successful login**: the user's token doesn't
  carry `librechat-user` in `realm_access.roles` -- see `docs/KEYCLOAK.md`
  step 4 for how to check this with jwt.io.
- **`OPENID_ISSUER` mismatch**: must exactly match Keycloak's own issuer
  string for that realm, including trailing slash conventions -- copy it
  from `https://<keycloak-host>/realms/<realm>/.well-known/openid-configuration`'s
  `"issuer"` field rather than typing it by hand.

## RTL / Persian text rendering oddly

Expected in some cases and worth checking against LibreChat's current
release before assuming it's this stack's problem -- see the launch plan's
RTL verification step. LibreChat is MIT-licensed, so a real rendering bug
(bidi isolation on inline identifiers, code blocks inheriting the
paragraph's direction) is a patchable frontend fix, not a blocker to route
around.

## The knowledge base

Full guide: [`KNOWLEDGE_BASE.md`](KNOWLEDGE_BASE.md). The failure modes that
actually come up:

**`kb_search` returns nothing, and the agent says the catalog is empty.** It
probably is. `make kb-status` shows the counts; `make kb-sources` shows whether
any source is `enabled: true` and whether `approved` is on. A fresh install has
every source disabled on purpose.

**`{"error": {"type": "not_approved"}}`.** Syncing is switched off. The message
says which switch did it -- `approved: false` in `config/kb-sources.yaml`, or
`KB_SOURCES_APPROVED=false` in the environment, which wins over the file.

**`{"error": {"type": "missing_credential"}}` naming `KB_CONFLUENCE_*` or
`GITLAB_TOKEN`.** Sync needs a read-only *service account*: it has to be able to
see a space in order to catalog it. The per-user credentials LibreChat injects
into `mcp-agent-skills` are a different thing and are not used here.

**Entries exist but have no summaries.** The summarizer was unset or unreachable
when they were catalogued; they are findable by title and location in the
meantime. Set `KB_SUMMARIZER_URL`/`KB_SUMMARIZER_MODEL` and re-sync.

**The nightly full pass runs at the wrong hour.** `at: "03:00"` is read in
`KB_TIMEZONE`, which defaults to UTC -- set it to the zone the people who wrote
that config live in.

**The catalog stopped updating.** `make logs SERVICE=kb-scheduler`. A source
that fails is recorded against its run and retried at its next cadence, not
every tick, so one broken source looks like silence for that source only.
`make kb-status` lists the recent runs with their errors.

**`mcp-kb` is healthy but LibreChat doesn't list `kb_search`.** The server is
registered in `config/librechat.yaml.example`; the generated config needs
re-rendering and `api` restarting: `make render-config && make restart
SERVICE=api`. The tools also have to be attached to the agents
(`make agent-import`).
