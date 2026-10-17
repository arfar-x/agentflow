# End-to-end run

One script that starts the admin console, the scheduler and the MCP server as real
processes against a throwaway Postgres database, drives the console in Chromium,
and checks what a browser cannot: the database, the sources file, and what an
agent gets back from `kb_search` / `kb_get`.

Only two things are stand-ins, both in this directory: a Confluence look-alike
(`fake_confluence.py`, which insists on the bearer token) and an OpenAI-compatible
model endpoint (`llm_stub.py`). Everything else is the code that ships.

It is not part of `pytest` or `vitest`: it needs Postgres and a browser.

```bash
# once
(cd kb/e2e && npm install && npx playwright install chromium)
# a Postgres with pg_trgm that the script may create a database in
export E2E_PG_ADMIN_DSN=postgresql://postgres:…@127.0.0.1:5432/postgres
# the console's built web app
(cd kb/frontend && npm ci && npm run build)

make kb-e2e                       # or: python kb/e2e/run.py [scenario-name …]
```

| Variable | Default | |
|---|---|---|
| `E2E_PG_ADMIN_DSN` | `postgresql://postgres@127.0.0.1:55432/postgres` | A superuser DSN. The script drops and recreates the database `kb_e2e`. |
| `E2E_WORK` | a new temp directory | Logs, the working `kb-sources.yaml`, screenshots and every API response the browser saw. |
| `E2E_CHROMIUM` | `/opt/pw-browsers/chromium` | The browser executable. |
| `E2E_PLAYWRIGHT_FROM` | this directory | Where `node_modules/playwright` is found. |

Ports used: 8324 (console), 18322 (MCP), 18090 (fake wiki), 18080 (fake model).

## What it covers

1. Sign-in and what is refused without it
2. Adding a source from the console: the file, its comments and `${VAR}` placeholders, the revision history, the DB mirror
3. Enabling it and keeping its text; the first sync starting by itself; dry run changes nothing; a full sync updates only what changed
4. What an agent sees through `mcp-kb`: search, and `kb_get` paging a long document
5. The wiki changes: an incremental pass picks up edits but never concludes a page is gone; a full pass does
6. Failures: a broken wiki is a recorded error, Issues shows it, the next request works; a scheduler without its credential is named in Issues, never its value
7. Manual records: written from an unanswered search, summary drafted, published, found in Persian, read with `kb_get`, summary-only edit, delete
8. The syncing switch refuses a requested sync but still publishes a manual record
9. A hand edit between preview and apply is a 409 and is recorded
10. Deleting a source retires its entries and their kept text
11. The scheduler is killed mid-run: the run and its request are closed, and a restart does not make every cadence due
12. No credential in any API response or log, and none in the console's environment
13. Guessing the token is locked out; a script holding it is not

Pass scenario names (a substring is enough) to run some of them, e.g.
`python kb/e2e/run.py add_source`; later scenarios depend on earlier ones, so
that is for debugging, not for CI.
