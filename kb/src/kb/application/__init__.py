"""Use cases and the ports they need.

Depends on `kb.domain` and on nothing else -- no database driver, no HTTP
client, no MCP framework. Everything that touches the outside world arrives as
a protocol implementation, which is what lets the behavior below be tested
with fakes and the adapters be swapped without rewriting it.
"""
