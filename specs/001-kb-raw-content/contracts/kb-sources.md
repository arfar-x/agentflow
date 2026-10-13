# Contract: `config/kb-sources.yaml` additions

```yaml
defaults:
  raw_content_max_bytes: 10485760   # optional; per-document cap, >= 1024

sources:
  - id: gitlab-platform
    kind: gitlab
    # Keep each document's full text in the catalog, readable through kb_get
    # by EVERY knowledge base user, whatever their access in GitLab.
    store_raw_content: true
    raw_content_max_bytes: 2097152   # optional per-source override
```

- `store_raw_content` is valid on any source kind and defaults to `false`.
- `defaults.store_raw_content` is a configuration error:
  `store_raw_content is decided per source; set it on each source that should share its text, not under defaults`.
- `make kb-sources` shows each source's effective `store_raw_content` and `raw_content_max_bytes`.
