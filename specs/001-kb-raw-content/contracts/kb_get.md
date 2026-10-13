# Contract: `kb_get` (MCP tool, read-only)

## Input

| Argument | Type | Default | Notes |
|---|---|---|---|
| `id` | string | required | An entry id from `kb_search` |
| `offset` | integer | `0` | Character position to read stored text from. Negative values are treated as 0. |

## Output (success)

```json
{
  "entry":  { "...": "unchanged: the entry, as before" },
  "fetch":  {"tool": "confluence_get_page", "args": {"page_id": "123"}},
  "stale":  false,
  "hidden": false,
  "content": {
    "text": "# ADR-007: Use Postgres for ...",
    "offset": 0,
    "total_chars": 51234,
    "next_offset": 24000,
    "truncated": false,
    "original_bytes": 61002
  }
}
```

- `content` is `null` when the source doesn't store raw content, the entry has no stored text,
  the stored text belongs to a different source version, or the entry is soft-deleted.
- `content.text` holds at most 24,000 characters.
- `next_offset` is `null` on the last page. Reading at or past the end returns `text: ""` with
  `next_offset: null`.
- `truncated: true` means the catalog holds only the first part of the document, up to its size
  limit. `original_bytes` is the full document's size. The rest is at `entry.location.url`.
- `fetch` keeps its existing meaning. When no live tool can read the source but text is stored,
  it is `{"tool": "kb_get", "args": {"id": "<id>"}}`.

## Output (errors, unchanged)

- `{"error": {"type": "not_found", ...}}`
- `{"error": {"type": "catalog_unavailable", ...}}`
- A non-integer `offset` is rejected by the tool schema itself (fastmcp validates arguments before
  the tool runs), like any other mistyped argument.

## `kb_search` (changed only in `fetch`)

A hit's `fetch` follows the same fallback rule. Search never returns or matches stored text.
