# Implementation Plan: Opt-in raw content for knowledge base sources

**Branch**: `feat/kb-raw-content` | **Date**: 2026-10-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-kb-raw-content/spec.md`

## Summary

Sync already holds every document's body when it summarizes it. For a source with
`store_raw_content: true`, keep that body in a new `entry_content` table:

- tied to the entry's `source_version`;
- capped at `raw_content_max_bytes` (default 10 MB), truncated at a character boundary beyond it
  and marked as truncated;
- removed when the document disappears or the flag is turned off.

`kb_get` gains an `offset` argument and returns the text in pages of about 24,000 characters. A
search hit whose document has no live fetch tool but has stored text gets `fetch: kb_get`. That
way the existing agent instruction "follow the hit's `fetch` field" reaches the full text with no
agent changes.

Everything goes through the existing single write funnel, `ReconcileDocument`, so all four
freshness mechanisms behave the same. `mcp-kb` stays read-only and credential-free.

## Technical Context

**Language/Version**: Python 3.12 (`kb/`)

**Primary Dependencies**: pydantic v2 (models, also in domain), psycopg 3 (store adapter), fastmcp
(MCP front door), PyYAML (config)

**Storage**: the catalog's own Postgres (`kb-db`). One new table, `entry_content`, added by
migration `003_entry_content.sql`, which `make kb-init` applies.

**Testing**: pytest. Unit tests run with in-memory fakes (`tests/application/fakes.py`) and no
services. Postgres tests live in `tests/integration/` and run under `make kb-test`. The two gates
are `tests/test_spec_coverage.py` and `tests/test_boundaries.py`.

**Target Platform**: Linux containers: `kb-scheduler` and the CLI (writers) and `mcp-kb` (reader).

**Project Type**: a library with a CLI, an MCP server and a scheduler (hexagonal).

**Performance Goals**:
- A `kb_get` page read is one indexed primary-key lookup with `substr()` in SQL, so it never moves
  more than one page plus metadata to `mcp-kb`.
- Search cost is unchanged apart from one `entry_id = ANY(...)` existence check over the hits
  already found.

**Constraints**:
- No model call to store or refresh text (FR-005, SC-004).
- No text in search (FR-012).
- The reader role stays SELECT-only on the new table (FR-013).
- Text is always valid UTF-8 after truncation.

**Scale/Scope**: thousands of text documents per source. At about 2–20 KB per ADR, 1,000 ADRs come
to roughly 20 MB before TOAST compression. The 10 MB cap is per document.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
|---|---|---|
| I. Configuration over code | A setting in `config/kb-sources.yaml`. No upstream component is patched. | Pass |
| II. Spec of record leads | `docs/spec/knowledge-base.md` is amended in the first implementation commit: N1/FR-ENT-01 narrowed, C6 added, `FR-CNT-*` added with status `planned`. Each status flips to `done` with the commit that tests it. | Pass |
| III. Hexagonal, provable core | Truncation and paging are pure domain functions. Use cases get the content policy as an argument and use new port methods, proven against fakes. SQL stays in the adapter. | Pass |
| IV. Secrets / internal services | No new secret, service or port. | Pass |
| V. Least privilege | `mcp-kb` only SELECTs the new table: no write grant, no source credentials. Storing is an explicit per-source opt-in in tracked config, never a default (FR-001/002). | Pass |
| Constraint: no new infrastructure | Same database, same services. | Pass |
| Constraint: bilingual | Truncation is character-safe for Persian (multi-byte UTF-8). | Pass |

Re-checked after Phase 1 design: no violations, and nothing in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/001-kb-raw-content/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── kb_get.md        # MCP tool contract (agent-facing)
│   └── kb-sources.md    # configuration contract (operator-facing)
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
docs/spec/knowledge-base.md                 # spec of record: N1, FR-ENT-01, C6, FR-CNT-*, §8, change log
docs/KNOWLEDGE_BASE.md                      # operator guide: the switch, exposure, size, revoke
config/kb-sources.yaml.example              # commented store_raw_content on the GitLab source
kb/
├── migrations/003_entry_content.sql        # new table + kb_reader SELECT grant
├── src/kb/
│   ├── domain/content.py                   # new: ContentPolicy, RawContent, ContentPage, prepare(), page bounds
│   ├── domain/entry.py                     # fetch hint falls back to kb_get when text is stored
│   ├── sources_config.py                   # store_raw_content, raw_content_max_bytes, content_policy_for()
│   ├── application/ports/entry_store.py    # put/delete/purge/read content, content_ids; mark_missing drops text
│   ├── application/use_cases/
│   │   ├── reconcile_document.py           # execute(..., content=policy): store/refresh/delete text
│   │   ├── sync_source.py                  # passes policy; purges when off; reports counts
│   │   ├── refresh_entries.py              # per-source policy resolver
│   │   ├── get_entry.py                    # EntryView.content page (version-matched, not for deleted)
│   │   └── search_catalog.py               # has_content per hit -> fetch: kb_get
│   └── adapters/
│       ├── outbound/postgres_store.py      # SQL for the new port methods
│       └── inbound/{mcp_server,cli,scheduler}.py  # kb_get(offset), wiring the policy
└── tests/
    ├── domain/test_content.py
    ├── application/{fakes.py,test_reconcile_content.py,test_sync_content.py,test_get_entry_content.py,…}
    ├── adapters/test_mcp_*.py, test_sources_config*.py
    └── integration/test_postgres_content.py
```

**Structure Decision**: the existing `kb/` hexagonal layout. One new domain module
(`content.py`), no new adapter, port methods added to `EntryStore`, one new migration file
(migrations are append-only).

## Complexity Tracking

No constitution violations to justify.
