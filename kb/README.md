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
| 5 | CLI and MCP server — usable from LibreChat | next |
| 6 | Sources: Confluence, Jira, GitLab, HTTP API, local files | |
| 7 | Freshness mechanisms, scheduler, webhook receiver | |
| 8 | Agent tools and instructions | |
| 9 | Operator documentation | |

**There is no front door yet** — no CLI, no MCP server, so nothing in LibreChat
can reach the catalog until phase 5. What exists is the behavior (phases 2–3)
and real storage behind it (phase 4): entries can be written, searched, ranked,
overridden and soft-deleted in Postgres today, from Python or a test.

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

### Against the real database

Once the stack is up and `make kb-init` has run, the store is usable directly:

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
