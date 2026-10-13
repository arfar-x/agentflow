# Quickstart: validating opt-in raw content

Prerequisites:
- the stack is up (`make up`);
- the migration is applied (`make kb-init`);
- a GitLab source with ADRs is configured and enabled in `config/kb-sources.yaml`
  ([contract](contracts/kb-sources.md)).

1. **Unit and integration suites**: `cd kb && python -m pytest`, then `make kb-test`. Both are
   green, and the spec-coverage gate counts every `FR-CNT-*` marked `done`.
2. **Off by default (US2, SC-002)**:
   1. Run `make kb-sync SOURCE=gitlab-platform`. The report shows `content_stored: 0`.
   2. Run `docker compose run --rm kb-cli get --id <an ADR entry id>`. It shows `content: null`.
3. **Turn it on (US1, SC-004)**:
   1. Set `store_raw_content: true` on the source.
   2. Run `make kb-sync SOURCE=gitlab-platform` (full mode by default). The report shows `content_stored` equal
      to the documents seen, and `summarized: 0` for an already-catalogued source.
   3. `get --id <id>` now returns `content.text`, with `next_offset` set when the ADR is over 24k
      characters. `get --id <id> --offset <next_offset>` reads the next page.
4. **Agent (SC-001)**:
   1. In LibreChat, ask an agent that has `kb_search`/`kb_get` a question answered only in an
      ADR's body.
   2. The hit's `fetch` names `kb_get`, and the agent answers from `content`
      ([contract](contracts/kb_get.md)).
5. **Truncation (US4)**:
   1. Set `raw_content_max_bytes: 2048` on the source and sync a larger file.
   2. `content.truncated: true` and `original_bytes` is greater than 2048.
6. **Revoke (US3, SC-003)**:
   1. Set `store_raw_content: false` and run any sync of the source.
   2. The report shows `content_purged > 0`. `get` returns `content: null`, and
      `SELECT count(*) FROM entry_content JOIN entry ON id = entry_id WHERE source_id = '…'` is 0.
7. **Search unchanged (SC-005)**: `docker compose run --rm kb-cli search "…"` returns the same ids in the same order
   with the flag on and off.
