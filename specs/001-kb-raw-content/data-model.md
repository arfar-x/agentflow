# Data model: Opt-in raw content

## Configuration (operator-facing)

| Field | Where | Type | Default | Rules |
|---|---|---|---|---|
| `store_raw_content` | each source | bool | `false` | Rejected under `defaults` (FR-002). |
| `raw_content_max_bytes` | `defaults` and/or each source | int | 10,485,760 | ≥ 1024. A source value wins over `defaults`. |

`SourcesConfig.content_policy_for(source) -> ContentPolicy` resolves both.

## Domain (`kb/src/kb/domain/content.py`)

All models are frozen pydantic models with `extra="forbid"`.

- **ContentPolicy** `{store: bool, max_bytes: int}`. `ContentPolicy.off()` is the default
  everywhere.
- **RawContent** `{text: str, truncated: bool, original_bytes: int}`, built by
  `prepare(body, max_bytes) -> RawContent | None`. Returns `None` for an empty or whitespace-only
  body. Truncation is a UTF-8 byte cut followed by a lossless-prefix decode.
- **ContentPage** `{text: str, offset: int, total_chars: int, next_offset: int | None,
  truncated: bool, original_bytes: int, source_version: str | None}`.
- `CONTENT_PAGE_CHARS = 24_000`. `page_bounds(offset, total)` clamps a negative offset to 0, and
  `next_offset(offset, returned, total)` gives the next position or `None`.

## Storage (`kb/migrations/003_entry_content.sql`)

```text
entry_content
  entry_id        TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE
  source_version  TEXT            -- the version the text was read at
  body            TEXT NOT NULL   -- possibly truncated
  truncated       BOOLEAN NOT NULL DEFAULT FALSE
  original_bytes  BIGINT NOT NULL -- size of the full document in UTF-8 bytes
  stored_at       TIMESTAMPTZ NOT NULL DEFAULT now()
GRANT SELECT ON entry_content TO kb_reader  (when the role exists)
```

## Port (`EntryStore`) additions

| Method | Meaning |
|---|---|
| `put_content(entry_id, content: RawContent, *, source_version)` | Upsert; no rewrite when the version and body are unchanged. |
| `delete_content(entry_id)` | Remove text for one entry (empty body, or the policy turned off mid-funnel). |
| `purge_content(source_id) -> int` | Remove all text for a source; returns the rows deleted. |
| `read_content(entry_id, *, offset, limit) -> ContentSlice \| None` | One slice plus metadata, or `None`. |
| `content_ids(entry_ids) -> set[str]` | Which of these entries have text (for search hints). |
| `mark_missing(...)` (changed) | Also deletes text for the ids it soft-deletes (FR-007). |

`ContentSlice` is an application-level model `{text, offset, total_chars, truncated,
original_bytes, source_version}`. The domain turns it into a `ContentPage`.

## Lifecycle

```text
absent ──sync(store on, body non-empty)──▶ stored(v)
stored(v) ──sync(store on, version v')──▶ stored(v')
stored(v) ──sync(store on, empty body)──▶ absent
stored(*) ──sync(store off, any mode)──▶ absent      (purge_content)
stored(*) ──full sync: document missing──▶ absent    (mark_missing)
stored(*) ──entry row deleted──▶ absent              (ON DELETE CASCADE)
```

Read rule: text is served only when `content.source_version == entry.source_version` and
`entry.deleted_at is None`.
