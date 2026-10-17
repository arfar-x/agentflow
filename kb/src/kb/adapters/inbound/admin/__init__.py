"""The admin console's web door (spec §6.11).

A human's front door, not an agent's: nothing here is an MCP tool, and it never
writes the catalog -- it writes requests down for the scheduler to act on. See
`app.build_app`.
"""

from kb.adapters.inbound.admin.app import AdminServices, build_app

__all__ = ["AdminServices", "build_app"]
