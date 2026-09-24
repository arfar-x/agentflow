# Knowledge base

The catalog of **what knowledge exists in your organization and exactly where it
lives**, searchable by the agents. It holds pointers and multilingual summaries,
never document bodies: an agent finds the right document here, then reads it
live through the Jira/Confluence tools with the asking user's own credentials.

This is the operator's guide. The design and its requirements are
[`docs/spec/knowledge-base.md`](spec/knowledge-base.md); the module's own
internals are [`kb/README.md`](../kb/README.md).

## What runs

| Service | What it does | Exposed |
|---|---|---|
| `kb-db` | The catalog's own Postgres, separate from LibreChat's `vectordb` | no port |
| `mcp-kb` | `kb_search` / `kb_get` for the agents. Read-only role, no credentials | no port |
| `kb-scheduler` | Syncs on the configured cadences, drains the refresh queue | no port |
| `kb-cli` | One command and exits (`make kb-*`). Connects as the database owner | not running |
| `kb-webhook` | GitLab push events. Opt-in (`--profile webhook`) | loopback only |

`make up` starts the first three and runs `scripts/kb-bootstrap.sh`, which
creates the schema, the least-privilege query role, and `config/kb-sources.yaml`
if it is missing — then prints whatever is still needed.

## Getting it filling

### 1. Credentials

Sync needs a **read-only service account**: it has to see a space in order to
catalog it. This is *not* the per-user credential an agent reads a page with —
that check still happens at the source, as the user.

```bash
# .env
KB_CONFLUENCE_BASE_URL=https://wiki.internal   # falls back to CONFLUENCE_* if unset
KB_CONFLUENCE_USERNAME=kb-bot
KB_CONFLUENCE_PASSWORD=...                     # or KB_CONFLUENCE_PAT
KB_JIRA_BASE_URL=https://jira.internal         # falls back to JIRA_*
KB_JIRA_PAT=...
GITLAB_BASE_URL=https://gitlab.internal
GITLAB_TOKEN=...                               # read_api scope is enough

KB_SUMMARIZER_URL=${VLLM_BASE_URL}             # the BASE url, ending in /v1
KB_SUMMARIZER_MODEL=<the model it serves>      # see its GET /v1/models
KB_SUMMARIZER_API_KEY=${VLLM_API_KEY}          # empty if the endpoint needs none
KB_SUMMARY_LANGUAGES=en,fa                     # what makes cross-language search work
KB_TIMEZONE=Asia/Tehran                        # `at: "03:00"` means 03:00 here
```

Without the summarizer everything still runs — documents are catalogued under
their real titles, just undescribed, and cross-language search will not work.

Then confirm it is actually usable, rather than merely configured:

```bash
make kb-check
```

It reports each dependency separately — the database, the summarizer, the
source configuration — and names the one that is not ready. The summarizer
check asks the endpoint `GET <url>/models`, which is the call every
OpenAI-compatible server answers: it proves the protocol, and reports whether
the model you named is among the ones served. (A vLLM deployment can advertise
an id different from what it serves, so that part is reported, not enforced.)

The URL is the **base**, ending in `/v1`. A pasted `/v1/chat/completions` is
accepted and trimmed; something that is not a URL fails at startup.

### 2. Choose sources

```bash
make kb-sources-discover          # what Confluence, Jira and GitLab actually contain
make kb-sources-discover WRITE=1  # append them to config/kb-sources.yaml, disabled
```

Discovery reports sizes (`412 pages -- Engineering`, `docs/ADRs: 14 files`) so
the decision is "yes, catalog this one" rather than "what exists?". Then edit
`config/kb-sources.yaml`: delete what does not belong, set `enabled: true` on
what does. Re-running discovery later **never rewrites a line you edited** — it
only appends sources it has not seen, which makes it also the answer to "what is
new that the catalog isn't watching?".

Useful knobs, all optional:

```yaml
  - id: confluence-eng
    kind: confluence
    enabled: true
    spaces: [ENG, PRODUCT]
    # A page's labels decide its entry type -- curation happens in Confluence,
    # in the tool the authors already use.
    type_from_labels: { kb-glossary: term, kb-product: product, kb-team: team }
    exclude_labels: [archive, draft]
```

### 3. First sync

```bash
make kb-sync SOURCE=confluence-eng DRY_RUN=1   # what it would read; writes nothing
make kb-sync SOURCE=confluence-eng             # for real
make kb-status
```

Then leave it alone: `kb-scheduler` takes over.

### 4. Let the agents use it

```bash
make render-config && make restart SERVICE=api   # LibreChat picks up mcp-kb
make agent-import DRY_RUN=1 && make agent-import # tools + instructions onto the agents
```

The front-door agent is instructed to search the catalog **before** answering or
delegating, in the user's language and the other one, to follow each hit's
`fetch` field and read the real document, to say so and ask when the catalog has
nothing, and to name what it used.

## How it stays current

Four mechanisms, each switchable per source in `config/kb-sources.yaml`:

| Mechanism | Default | What it is for |
|---|---|---|
| `incremental` | on, `every: 15m` | The backbone: asks each source only what changed since its checkpoint |
| `lazy_refresh` | on, `stale_after: 24h` | `kb_get` on a stale entry queues it; the scheduler re-reads that one document |
| `full_scrape` | on, `at: "03:00"` | The only pass that detects deletions, moves and missed events |
| `webhook` | off | GitLab push events, for seconds instead of minutes |

Two properties hold everywhere, and are worth knowing before tuning anything:

- **An unchanged document costs nothing.** Content is hashed, so a re-run makes
  no model call and no write. A 15-minute cadence over a quiet wiki is close to
  free.
- **Only a full pass may conclude something is gone.** An incremental run only
  ever sees what changed, so treating its silence as deletion would empty the
  catalog. Deletions are soft — the row stays, with its date.

### The GitLab webhook (optional)

```bash
# .env
KB_WEBHOOK_SECRET=<long random string>
```

```bash
docker compose --profile webhook up -d kb-webhook
# GitLab: Settings > Webhooks > URL http://<host>:8323/gitlab, Secret token = the same
```

It validates the secret, queues the changed paths, and returns. The scheduler —
which holds the credentials and the writable role — does the refreshing. So the
worst a forged request can achieve is a re-read of documents you already have.

## Day to day

```bash
make kb-status                     # counts by source and type, overrides, gaps, migrations
make kb-sources                    # what is configured, and which switch each mechanism is on
make kb-sync SOURCE=<id> [MODE=incremental] [DRY_RUN=1]
docker compose run --rm kb-cli gaps          # searches that found nothing
docker compose run --rm kb-cli override set --id <entry> --summary 'en=Better text' --note 'why'
docker compose run --rm kb-cli override clear --id <entry>
make logs SERVICE=kb-scheduler
```

**Gaps are the most useful thing here.** Every search that matched nothing is
recorded, so `gaps` is a ranked list of what your organization has not written
down — generated from real questions rather than guesses.

**Overrides** are corrections that survive every later sync. The entry keeps
being regenerated from its source; your text is re-applied on top. `exclude`
retires an entry from search without touching anything upstream.

### Switching syncing off

```bash
# in config/kb-sources.yaml
approved: false
# or, without editing anything -- and this wins over the file
KB_SOURCES_APPROVED=false
```

Per-source `enabled:` is the everyday control; `approved` is the one switch for
an incident. Every command reports which of the two it used.

## Backup and restore

`kb_data` is in `make backup` like every other volume. What is actually
irreplaceable in it is small: the **overrides**, the **gap log** and the **sync
checkpoints**. Entries and the search index are derived — losing them costs one
`make kb-sync` per source, not a restore.

Schema changes are migrations in `kb/migrations/`, applied by `make kb-init`
(which `make up` runs). They are append-only: a shipped file is never edited, so
an upgrade is always "apply what is new, skip what is recorded".

## When something looks wrong

| Symptom | Where to look |
|---|---|
| `kb_search` returns nothing | `make kb-status` — is anything catalogued? Then `make kb-sources` — is a source enabled, and is `approved` true? |
| The catalog is not updating | `make logs SERVICE=kb-scheduler`. A failing source is recorded and retried at its next cadence, not every tick |
| `{"error": {"type": "not_approved"}}` | Syncing is switched off; the message says whether it was the file or `KB_SOURCES_APPROVED` |
| `{"error": {"type": "missing_credential"}}` | The named variables are unset — sync needs a service account, not a user's credential |
| `make kb-check` says the summarizer is not ok | The error is in the report: a 401 means `KB_SUMMARIZER_API_KEY`, "not an OpenAI /models list" means the URL points at something else (a proxy, a login page) |
| Entries exist but have no summaries | `KB_SUMMARIZER_URL`/`KB_SUMMARIZER_MODEL` are unset, or the endpoint was down when they were catalogued. Re-syncing after fixing it fills them in |
| The nightly pass runs at the wrong hour | `KB_TIMEZONE`. `at: "03:00"` is read in that zone; the default is UTC |
| An agent answers without searching | `make agent-import` — the instruction and the two tools live in `agents/*.yaml` |

## Why it is shaped this way

Three constraints explain most of the design, and are worth keeping in mind
before changing it:

- **The catalog never stores document text.** Answers come from the live
  document, read as the user, so source permissions keep working and nothing
  goes stale behind your back.
- **Reading and writing are different privileges.** `mcp-kb` has a read-only
  database role and no source credentials at all; `kb-scheduler` and `kb-cli`
  have both. That is why a stale entry read by an agent is *queued* for refresh
  rather than refreshed on the spot.
- **Nothing a model can call may write.** Both MCP tools are read-only, which is
  also why neither needs a `toolApproval` entry.
