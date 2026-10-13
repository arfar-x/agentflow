# Tasks: Opt-in raw content for knowledge base sources

**Input**: Design documents from `specs/001-kb-raw-content/`
(spec.md, plan.md, research.md, data-model.md, contracts/)

**Tests**: Required. Constitution III and II require every `FR-CNT-*` that is marked `done` in
`docs/spec/knowledge-base.md` to have a test carrying `# Covers: FR-CNT-..`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 read a shared document · US2 summary-only by default · US3 revoke · US4 large
  documents

## Requirement ids in the spec of record

The feature-spec ids map to spec-of-record ids as follows.

| Spec of record | Feature spec | Meaning |
|---|---|---|
| FR-CNT-01 | FR-001, FR-002 | `store_raw_content` per source, default false; rejected under `defaults` |
| FR-CNT-02 | FR-003 | Opted-in sync stores text at the entry's source version |
| FR-CNT-03 | FR-004 | No text stored for a source without the flag |
| FR-CNT-04 | FR-005 | Storing text never causes a model call by itself |
| FR-CNT-05 | FR-006 | Flag off → the next sync purges the source's text |
| FR-CNT-06 | FR-007 | Missing or removed entry → its text is deleted |
| FR-CNT-07 | FR-008, FR-009 | Configurable cap (default 10 MB); over-cap text stored truncated at a character boundary, marked, with original size |
| FR-CNT-08 | FR-010, FR-011 | `kb_get` pages text with total, truncated flag and next offset; consecutive pages reproduce it exactly |
| FR-CNT-09 | FR-012 | Search never matches or returns text |
| FR-CNT-10 | FR-013 | The query role may only SELECT stored text |
| FR-CNT-11 | edge cases | Text is served only for the entry's current version and never for a soft-deleted entry |
| FR-CNT-12 | FR-016 | With no live fetch tool, a hit or entry with text names `kb_get` as its fetch |
| FR-CNT-13 | FR-014 | `kb_get`'s description tells agents when to answer from `content` and when to fall back |

FR-015 (operator documentation) is delivered in `docs/KNOWLEDGE_BASE.md` (T026) and isn't an
executable requirement.

---

## Phase 1: Setup — spec of record (commit: `docs(spec): …`)

- [X] T001 Amend `docs/spec/knowledge-base.md`:
  - narrow N1 and FR-ENT-01 ("the entry never holds a body; a separate content store may, for
    opted-in sources");
  - add C6 (the sharing consequence) and §6.x `FR-CNT-01…13` with status `planned`;
  - update the §8 `kb_get` row (the `offset` argument and `content`) and the §8 config example;
  - bump the version to 2.4.0 and add a change-log row.
- [X] T002 Run `cd kb && python -m pytest tests/test_spec_coverage.py`. `planned` ids with no
  tests must pass.

## Phase 2: Foundational — domain + config (commit: `feat(kb): …content policy…`)

- [X] T003 [P] Create `kb/src/kb/domain/content.py`:
  - `ContentPolicy` (with `off()`), `RawContent`, `ContentPage`;
  - `CONTENT_PAGE_CHARS = 24_000`, `DEFAULT_RAW_CONTENT_MAX_BYTES = 10_485_760`,
    `MIN_RAW_CONTENT_MAX_BYTES = 1024`;
  - `prepare(body, max_bytes)`: returns `None` for an empty or whitespace body; cuts UTF-8 bytes
    at `max_bytes` and decodes with `errors="ignore"`;
  - `page(text_slice, offset, total, …) -> ContentPage` with the `next_offset` rule.
- [X] T004 [P] Write `kb/tests/domain/test_content.py`:
  - no truncation under the cap;
  - truncation at the cap, marked, with `original_bytes`;
  - a Persian multi-byte character on the boundary is never split;
  - an empty body;
  - paging: the first, middle and last pages, offset ≥ total, negative offset.
  - Covers FR-CNT-07, FR-CNT-08.
- [X] T005 Extend `kb/src/kb/sources_config.py`:
  - `store_raw_content: bool = False` and `raw_content_max_bytes: int | None` (≥ 1024) on
    `_BaseSource`;
  - `raw_content_max_bytes` on `Defaults`;
  - an explicit `ConfigError` when `defaults.store_raw_content` is present;
  - `SourcesConfig.content_policy_for(source) -> ContentPolicy`.
- [X] T006 Write config tests in `kb/tests/` (next to the existing sources-config tests):
  - default false;
  - per-source true;
  - rejected under `defaults` with the setting named;
  - cap from defaults, overridden per source, below minimum rejected.
  - Covers FR-CNT-01, FR-CNT-07.
- [X] T007 Flip FR-CNT-01 and FR-CNT-07 to `done` in the spec. Run the whole kb suite.

## Phase 3: Foundational — storage (commit: `feat(kb): store raw content…`)

- [X] T008 Create `kb/migrations/003_entry_content.sql`:
  - the `entry_content` table exactly as in data-model.md (FK `ON DELETE CASCADE`);
  - an index for purge-by-source via the join on `entry.source_id`, which already exists;
  - a `GRANT SELECT ON entry_content TO kb_reader` guarded like 002.
- [X] T009 Extend `kb/src/kb/application/ports/entry_store.py`:
  - `put_content`, `delete_content`, `purge_content`, `read_content`, `content_ids`;
  - the `ContentSlice` model;
  - a `mark_missing` docstring saying it deletes text.
- [X] T010 Implement them in `kb/src/kb/adapters/outbound/postgres_store.py`:
  - a conditional upsert (`WHERE` version or body differs);
  - `substr`/`char_length` reads;
  - `DELETE … USING entry` purge;
  - `mark_missing` also deletes text;
  - `status()` reports the stored-content count and bytes.
- [X] T011 [P] Extend the in-memory fake in `kb/tests/application/fakes.py` to satisfy the port.
  `test_ports_models.py` already asserts this.
- [X] T012 [P] Write `kb/tests/integration/test_postgres_content.py` (skips without
  `KB_TEST_DATABASE_URL`):
  - round trip;
  - unchanged upsert is a no-op;
  - paging via SQL matches the domain;
  - cascade on entry delete;
  - `mark_missing` deletes text;
  - purge by source;
  - `kb_reader` can SELECT but not INSERT, UPDATE or DELETE.
  - Covers FR-CNT-06, FR-CNT-10.
- [X] T013 Flip FR-CNT-10 to `done`. Run the kb suite and `make kb-test`.

## Phase 4: User Story 2 + 1 (write side) — the sync funnel (commit: `feat(kb): keep raw content during sync…`)

- [X] T014 [US1] `kb/src/kb/application/use_cases/reconcile_document.py`: add
  `execute(document, *, force=False, content: ContentPolicy = ContentPolicy.off())`.
  - After `upsert`/`touch`/`revive`, when `content.store`, call `prepare()` then `put_content`.
    An empty body calls `delete_content`.
  - Add `content_stored: bool` to `ReconcileResult`.
  - The summarize decision is unchanged.
- [X] T015 [US2] `kb/src/kb/application/use_cases/sync_source.py`: add an
  `execute(..., content=ContentPolicy.off())` parameter.
  - Pass it to reconcile.
  - When the policy is off and the run isn't a dry run, `purge_content(source_id)` after the
    loop.
  - Add `content_stored` and `content_purged` to `SyncReport`.
- [X] T016 [US1] `kb/src/kb/application/use_cases/refresh_entries.py`: add an optional
  `resolve_content: Callable[[str], ContentPolicy]` (default: off) and pass the policy per entry.
- [X] T017 [US1] Wire the policy in `kb/src/kb/adapters/inbound/cli.py` (`sync`, plus `sources`
  showing the effective values) and `kb/src/kb/adapters/inbound/scheduler.py` (sync ticks and
  `_refresher`), from `config.content_policy_for(...)`.
- [X] T018 [P] [US1] Write `kb/tests/application/test_reconcile_content.py`:
  - stores at the version;
  - replaces on change;
  - unchanged document with the flag newly on stores text with `summarized=False`;
  - empty body deletes;
  - policy off stores nothing.
  - Covers FR-CNT-02, FR-CNT-03, FR-CNT-04.
- [X] T019 [P] [US3] Write `kb/tests/application/test_sync_content.py`:
  - flag off purges (`content_purged`);
  - dry run purges nothing;
  - a full sync's missing documents lose their text;
  - lazy refresh uses the per-source policy.
  - Covers FR-CNT-05, FR-CNT-06.
- [X] T020 Flip FR-CNT-02…06 to `done`. Run the kb suite.

## Phase 5: User Story 1 + 4 (read side) — `kb_get`, search hints (commit: `feat(kb): read raw content through kb_get…`)

- [X] T021 [US1] `kb/src/kb/application/use_cases/get_entry.py`: add `execute(entry_id, *,
  offset=0)`.
  - `EntryView.content: ContentPage | None`, read only when
    `content.source_version == entry.source_version` and the entry isn't deleted.
  - Add a `fetch` property using the fallback.
- [X] T022 [US1] `kb/src/kb/domain/entry.py`: add
  `fetch_hint_for(entry, *, has_content) -> dict | None`, which falls back to
  `{"tool": "kb_get", "args": {"id": …}}` when there's no live tool and text exists.
  - `kb/src/kb/application/use_cases/search_catalog.py`: one `content_ids` call per search;
    `SearchHit.has_content`; `fetch_hint` uses the fallback.
- [X] T023 [US1] `kb/src/kb/adapters/inbound/mcp_server.py`:
  - `kb_get(id, offset=0)` returns `content` per contracts/kb_get.md;
  - `bad_argument` for a non-integer offset;
  - update `INSTRUCTIONS`, `GET_DESCRIPTION` and `SEARCH_DESCRIPTION` ("never document text" is
    no longer true);
  - the CLI `get` gains `--offset`.
- [X] T024 [P] [US1] Write tests: `kb/tests/application/test_get_entry_content.py` (version
  mismatch and deleted entry give no content; pages chain exactly; truncated flag passes through)
  and MCP tests next to the existing ones (the payload shape, the description text, search hits
  with the `kb_get` fallback, Confluence keeping its live tool). Covers:
  - FR-CNT-08, FR-CNT-11, FR-CNT-12, FR-CNT-13;
  - FR-CNT-09 (search ids and order identical with and without stored text).
- [X] T025 Flip FR-CNT-08, 09, 11, 12, 13 to `done`. Run the kb suite and `make kb-test`.

## Phase 6: Polish — operator docs (commit: `docs(kb): …`)

- [X] T026 [P] `docs/KNOWLEDGE_BASE.md`, a "Sharing a source's full text" section:
  - what `store_raw_content` exposes and to whom;
  - the size cap and truncation;
  - that turning it on needs one full sync to fill unchanged documents;
  - how to revoke (set false, then sync);
  - backup notes (it's rebuildable).
- [X] T027 [P] `config/kb-sources.yaml.example`: a commented `store_raw_content` and
  `raw_content_max_bytes` on the GitLab source, with the exposure warning.
- [ ] T028 Run the quickstart.md validation against the live stack.

## Dependencies

- Phase 1 comes before everything.
- Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6.
- US2 (default off) is delivered by Phase 2 config plus Phase 4 purge. US1 needs Phases 4 and 5.
  US3 is Phase 4. US4 is Phase 2 truncation surfaced in Phase 5.
- [P] tasks within one phase touch different files.
