# Research: Opt-in raw content for knowledge base sources

## R1. Where the text comes from

- **Decision**: Store `SourceDocument.body` inside `ReconcileDocument.execute`, the single write
  funnel. That covers incremental sync, full sync, the webhook and lazy refresh alike.
- **Rationale**: Every mechanism already hands the document, body included, to this one call.
  Storing there means no new fetch and no second write path. It also follows `kb/AGENTS.md`
  ("one funnel for writes").
- **Alternatives considered**:
  - **Live proxy from `mcp-kb` with the source's service token.** Rejected: it gives the query
    service credentials it deliberately doesn't hold (spec 1.7.0, constitution V).
  - **Store inside `SyncSource` only.** Rejected: the webhook and lazy-refresh paths would never
    store or refresh text.

## R2. How the per-source decision reaches the funnel

- **Decision**: A `ContentPolicy(store: bool, max_bytes: int)` is passed per call:
  - `ReconcileDocument.execute(document, *, force=False, content=ContentPolicy.off())`;
  - `SyncSource.execute(source, ..., content=...)`;
  - `RefreshEntries` takes a `source_id -> ContentPolicy` resolver beside its source resolver.
  - `SourcesConfig.content_policy_for(source)` builds the policy.
- **Rationale**: The CLI and the scheduler reload `kb-sources.yaml` on every run or tick. A policy
  bound at construction (`container.build`) would go stale until the process restarted.
  Defaulting to `off` keeps every existing caller, and every existing test, storing nothing.
- **Alternatives considered**:
  - **A resolver injected into `ReconcileDocument`'s constructor.** Rejected: the container
    doesn't load the sources file, and doing so would tie building the MCP server to that file.
  - **A field on `SourceDocument`.** Rejected: the adapter would decide policy, and
    "a source adapter maps, it does not decide".

## R3. Storage layout

- **Decision**: A separate table:

  ```sql
  entry_content(entry_id PK REFERENCES entry(id) ON DELETE CASCADE,
                source_version, body TEXT, truncated, original_bytes, stored_at)
  ```

  It is not a field on `entry.data`.
- **Rationale**:
  - `entry.data` is loaded on every search hit and every `get`. A 10 MB JSONB field there would
    make every search pay for the text.
  - The cascade makes "never outlives its entry" structural.
  - TOAST compresses large `TEXT` out of line automatically.
  - The search tables are untouched, so FR-012 holds by construction.
- **Alternatives considered**:
  - **Store the text in `entry.data`.** Rejected for the read cost above, and because `Entry` is
    documented as never holding a body.
  - **Object storage.** Rejected: new infrastructure.

## R4. Keeping text and entry on the same version

- **Decision**: Store the text with the `source_version` it was read at. `GetEntry` returns text
  only when that matches the entry's `source_version` and the entry isn't soft-deleted.
  - Writes are `INSERT … ON CONFLICT DO UPDATE … WHERE` the version or body differs, so an
    unchanged daily full pass doesn't rewrite rows.
  - An empty body deletes any stored row.
- **Rationale**: Reconcile writes the entry (or touches it) before writing the text. A crash
  between the two leaves a mismatch, which the read side turns into "no text" rather than a wrong
  answer. The next sync fixes it.

## R5. Truncation

- **Decision**: Encode as UTF-8 and cut at `max_bytes`, then decode with `errors="ignore"`. That
  drops at most one partial trailing character, so a Persian character is never split. Record
  `truncated=True` and `original_bytes`. The limit is `raw_content_max_bytes`: default
  10,485,760, settable in `defaults` and per source, with a minimum of 1 KB.
- **Rationale**: Bytes are what storage costs; characters are what reading is paged in. A cut on a
  byte limit with a character-safe decode serves both.

## R6. Paging

- **Decision**:
  - The page size is 24,000 characters, a domain constant (`CONTENT_PAGE_CHARS`), not an operator
    setting.
  - Offsets are character offsets.
  - The adapter reads `substr(body, offset+1, size)` and `char_length(body)` in SQL.
  - The domain computes `next_offset`: `None` at the end, and an empty page at or past the end.
- **Rationale**:
  - About 24k characters is roughly 6–8k tokens: comfortable for one tool result in a 32k–128k
    context, yet few round trips for a typical ADR (one page).
  - Postgres `substr` counts characters, matching Python string offsets.
  - Doing it in SQL means `mcp-kb` never loads 10 MB to return 24 KB.

## R7. How agents discover that text exists

- **Decision**:
  - `SearchCatalog` asks the store which hit ids have stored text (`content_ids`, one query).
  - When a hit has no live fetch tool (`fetch_hint is None`, e.g. GitLab) and has text, its
    `fetch` becomes `{"tool": "kb_get", "args": {"id": …}}`.
  - Sources with a live per-user tool (Confluence, Jira) keep that tool, which is current and
    enforces the user's own permissions; `kb_get` still returns their stored text when asked.
  - The `kb_get` and server instructions are updated to say the catalog may hold document text
    for some sources.
- **Rationale**: Every shipped agent (`agents/*.yaml`) already says "follow a hit's `fetch`
  field", so they reach the text with no agent changes. This is added to the spec as FR-016.
- **Alternatives considered**: a boolean `has_content` on hits that agents must interpret.
  Rejected: it needs every agent's instructions changed.

## R8. Turning the flag off

- **Decision**: `SyncSource.execute` deletes all text for the source (`purge_content(source_id)`)
  at the end of every non-dry run whose policy is off. `mark_missing` deletes text for the ids it
  soft-deletes. Both counts go in `SyncReport` (`content_stored`, `content_purged`).
- **Rationale**: It's idempotent and cheap: one indexed `DELETE … USING entry`, usually zero
  rows. Revocation happens on the next sync of any mode, which the scheduler runs every 15
  minutes by default.
