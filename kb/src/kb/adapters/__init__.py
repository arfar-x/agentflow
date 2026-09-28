"""Everything that touches the outside world.

`inbound/` is driven by someone else (the MCP server, the CLI, the webhook
receiver); `outbound/` is what the use cases drive (the store, the sources, the
summarizer). Only these modules may import a database driver, an HTTP client or
an MCP framework -- `tests/test_boundaries.py` enforces that.
"""
