# `kb` — the knowledge catalog

A searchable record of **what knowledge exists in the organization and exactly
where it lives**. It stores pointers and multilingual summaries, never document
bodies: an agent searches the catalog, then reads the real document live through
the tools it already has, with the calling user's own credentials.

The specification is [`docs/spec/knowledge-base.md`](../docs/spec/knowledge-base.md)
and it governs this module. Every requirement there has an id; every
implemented one has a test claiming it, and the suite fails if that stops being
true. Change the spec first, then the code.

## Status

| Phase | Contents | State |
|---|---|---|
| 1 | Specification | done |
| 2 | Pure rules: normalization, ids, hashing, ranking, override merging, validation | done |
| 3 | Use cases against in-memory fakes: search, reconcile | done |
| 4 | Storage: `kb-db`, migration, Postgres adapter | done |
| 5 | CLI and MCP server — usable from LibreChat | done |
| 6 | Sources: Confluence and Jira (done); GitLab, HTTP API, local files | in progress |
| 7 | Freshness mechanisms, scheduler, webhook receiver | next |
| 8 | Agent tools and instructions | |
| 9 | Operator documentation | |

**The catalog can now fill itself from Confluence and Jira.** Configure the
sources, review the file, and sync. GitLab, an HTTP API and local files are
specified but not built; scheduling all of this is phase 7, so today sync is
something you run.

## Getting it running

Python 3.12+, no services, no credentials:

```bash
cd kb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

Run everything that needs no services:

```bash
python -m pytest
```

Run the whole suite **including the Postgres-backed tests**, against a
throwaway database container that is removed afterwards:

```bash
cd .. && make kb-test          # or: scripts/kb-test.sh -k override -v
```

Those tests skip in a bare `pytest` run. To point them at a database you
already have:

```bash
KB_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db python -m pytest tests/integration
```

Useful variations:

```bash
python -m pytest tests/domain            # the pure rules only
python -m pytest tests/test_spec_coverage.py   # spec <-> tests agreement
python -m pytest -k persian -v           # one behavior, by name
```

The repo root's `make test` still runs only `agent-skills`' suite; `make
kb-test` is this module's.

### The CLI

Once the stack is up and `make kb-init` has run:

```bash
export KB_DATABASE_URL='postgresql://kb:<KB_POSTGRES_PASSWORD>@localhost:5432/agentflow_kb'

python -m kb status                                   # what the catalog contains
python -m kb search --query 'refund retries' --query 'بازپرداخت'
python -m kb get --id confluence-eng:123456
python -m kb override set --id confluence-eng:123456 \
    --summary 'en=A better summary' --note 'the generated one missed the retry cap'
python -m kb override clear --id confluence-eng:123456
python -m kb gaps                                     # what nobody has written down
python -m kb migrate                                  # same as make kb-init
```

`kb-db` publishes no port, so from the host use `docker compose exec kb-db`, or
run the CLI inside the `mcp-kb` container:
`docker compose exec mcp-kb python -m kb status`.

Every command prints one JSON document and exits 0, errors included:

```json
{"error": {"type": "database_unavailable", "message": "connection refused"}}
```

### Filling it

```bash
make kb-sources-discover            # what Confluence and Jira actually contain
make kb-sources-discover WRITE=1    # append them to config/kb-sources.yaml, disabled

# then edit config/kb-sources.yaml: enable what belongs, set reviewed: true

make kb-sources                     # what is configured, and what each mechanism is set to
make kb-sync SOURCE=confluence-eng DRY_RUN=1   # what would change; writes nothing
make kb-sync SOURCE=confluence-eng             # full pass: catalogs, and soft-deletes what vanished
make kb-sync SOURCE=confluence-eng MODE=incremental  # only what changed since the checkpoint
make kb-status
```

Sync needs a **read-only service account** (`KB_CONFLUENCE_*` / `KB_JIRA_*` in
`.env`, falling back to the stack's existing `CONFLUENCE_*`/`JIRA_*`): it has to
see a space in order to catalog it. That is a different thing from the per-user
credential an agent uses to read a page later — that check still happens at the
source, as the user.

The summarizer (`KB_SUMMARIZER_URL`/`KB_SUMMARIZER_MODEL`, any OpenAI-compatible
endpoint) is what writes each entry's title, summary and keywords in every
language in `KB_SUMMARY_LANGUAGES`. Leave it unset and sync still runs: entries
get their real titles and stay findable, just undescribed.

Two properties hold regardless of source:

- **A second sync over unchanged content makes no model calls and no writes.**
  Re-running is close to free; that is what makes a frequent cadence sane.
- **A full pass soft-deletes what the source no longer lists; an incremental one
  never does.** An incremental feed only yields what changed, so treating
  silence as deletion would empty the catalog.

### The MCP server

`mcp-kb` serves `kb_search` and `kb_get` on `http://mcp-kb:8322/mcp`, internal
to the backend network, with no credentials of its own — the catalog is shared.
It connects to Postgres as `kb_reader`, which can read and append to the gap log
and nothing else.

LibreChat picks it up from `config/librechat.yaml.example`'s `mcpServers.kb`
entry after `make render-config && make restart SERVICE=api`. Attaching the two
tools to agents is phase 8.

### Against the real database, from Python

```python
from kb.adapters.outbound.postgres_store import PostgresEntryStore
from kb.domain.text import normalize

store = PostgresEntryStore.connect("postgresql://kb:...@localhost:5432/agentflow_kb")
store.migrate()                               # idempotent; same thing make kb-init does
store.search(normalize("refund retries"))     # -> ranked entry ids
```

### Try the behavior by hand, with no database

The use cases take their dependencies as arguments, so the in-memory fakes from
the test suite are enough to exercise the real code:

```bash
cd kb && PYTHONPATH=src:. python
```

```python
from datetime import datetime, timezone
from kb.application.ports.knowledge_source import SourceDocument
from kb.application.use_cases.reconcile_document import ReconcileDocument
from kb.application.use_cases.search_catalog import SearchCatalog
from kb.domain.entry import EntryType, Location
from tests.application.fakes import FakeClock, FakeEntryStore, FakeSummarizer

store, summarizer = FakeEntryStore(), FakeSummarizer()
clock = FakeClock(datetime.now(timezone.utc))
reconcile = ReconcileDocument(store, summarizer, clock)

page = SourceDocument(
    source_id="confluence-eng",
    external_id="123456",
    title="Payment reconciliation",
    body="How refund retries are handled: three attempts with backoff.",
    location=Location(kind="confluence", ref={"page_id": "123456"}, url="https://wiki/123456"),
    type=EntryType.DOC,
    version="42",
)

print(reconcile.execute(page))
# entry_id='confluence-eng:123456' action=<Action.CREATED: 'created'> summarized=True
print(reconcile.execute(page))
# entry_id='confluence-eng:123456' action=<Action.UNCHANGED: 'unchanged'> summarized=False
print(len(summarizer.calls))          # 1 -- the second pass called no model

hit = SearchCatalog(store, clock).execute(["refund retries"]).hits[0]
print(hit.entry.title, hit.fetch_hint)
# {'en': 'Payment reconciliation'} {'tool': 'confluence_get_page', 'args': {'page_id': '123456'}}
```

The second `execute` printing `summarized=False` is the property the whole cost
model rests on: re-running sync over unchanged content makes no model calls and
writes nothing.

## Layout

```
src/kb/
├── domain/        pure rules -- stdlib and pydantic only, no I/O
│   entry.py       what an entry is, and its fetch hint
│   text.py        Persian/Arabic normalization (write and query side alike)
│   identity.py    deterministic ids, content hashes
│   ranking.py     reciprocal rank fusion
│   merge.py       overrides layered on generated entries
│   policies.py    is_stale / needs_resummarize / is_indexable
├── application/
│   ports/         protocols: EntryStore, KnowledgeSource, Summarizer, Clock
│   use_cases/     search_catalog, reconcile_document
└── adapters/
    outbound/      postgres_store.py  (phase 6+: confluence, gitlab, jira, api)
    inbound/       (phase 5: mcp server, cli, scheduler)
migrations/        001_initial.sql -- applied by `make kb-init`
```

Dependencies point inward: `adapters → application → domain`. `domain` and
`application` may not import a database driver, HTTP client, MCP framework or
YAML parser — [`tests/test_boundaries.py`](tests/test_boundaries.py) walks the
source and fails if they do. pydantic is allowed inside, because it is
validation rather than I/O, and it is what makes an invalid entry impossible to
construct on any path.

## Working on it

**Adding behavior.** Write the requirement in the spec first (an `FR-…`/`NFR-…`
row with status `planned`), implement it, add a test with a
`# Covers: FR-…` line, then flip the status to `done`. Forgetting either half
fails `tests/test_spec_coverage.py`, which is deliberate: it keeps
"implemented" from drifting into a claim nobody checked.

**Adding a source** (phase 6 onward) means one file in `adapters/outbound/`
implementing `KnowledgeSource`, and one entry in `config/kb-sources.yaml`.
Nothing in `domain/` or `application/` should change; if it does, the port is
wrong.

**Two properties worth protecting**, both already covered by tests:

- An unchanged document costs no model call and no write (`FR-REC-02`).
- An override outlives every later sync of its source (`FR-OVR-06`) — it lives
  in its own table and is re-applied when the searchable row is rebuilt, so sync
  can overwrite an entry freely without knowing overrides exist.
- An entry whose document could never be fetched cannot be constructed
  (`FR-ENT-02`) — "findable but unreadable" is the failure this whole module
  exists to prevent.
