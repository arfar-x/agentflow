## agentflow_mcp_auth

**Author:** agentflow
**Version:** 0.0.1
**Type:** tool + endpoint

### Description

This is agentflow-dify's own internal plugin, not a Marketplace listing.
It exists because Dify Community Edition has no per-end-user credential
mechanism for MCP tool providers (`MCPToolProvider.identity_mode` only
supports `off` or the Enterprise-only `idp_token` -- see this repo's root
`docs/CONFIGURATION.md`, "Per-user vs. shared credentials"). This plugin
is the "write a Dify plugin" upgrade path that doc describes: each Dify
end user stores their own Jira/Confluence credentials, and every call to
`mcp-agent-skills` is made under that specific user's identity, using the
exact mechanism `agent-skills/AUTHENTICATION.md` (Part 2) already
supports: an `X-Agent-Skills-Env-<VAR>` header per call, trusted because
`MCP_TRUST_REQUEST_CREDENTIALS=1` is set on that service.

It has two halves:

- **Tools** (`tools/`) -- `mcp_call_tool` (calls any agent-skills MCP tool,
  injecting the calling user's own stored credentials if they've set any),
  `mcp_list_tools` (discovery), `get_credentials_link` (hands the user a
  one-time link), `clear_my_credentials` (lets a user delete their own
  stored credentials).
- **Endpoint** (`group/credentials.yaml`, `endpoints/`) -- a small HTML
  form a user opens in their own browser to actually type their
  Jira/Confluence username and password/token. This is deliberate: a
  secret must never pass through the LLM's context or a conversation
  transcript, only the link to enter it does.

See `../../agent-skills/AUTHENTICATION.md` and this repo's root
`docs/CONFIGURATION.md` for the full reasoning; this file only covers
this plugin's own internals.

### Setup

Not installed by hand -- `scripts/bootstrap.sh` packages, signs, uploads,
and installs this plugin, creates its Endpoint instance, and configures
its one provider setting (`credentials_endpoint_url`) declaratively. See
this repo's root `docs/CONFIGURATION.md` for the exact steps and the
signing/trust model (`THIRD_PARTY_SIGNATURE_VERIFICATION_*`).

### Usage

Once installed and attached to an app's toolset in Studio:

1. A user asks the assistant to do something Jira/Confluence-related.
2. If they haven't set their own credentials yet, ask the assistant for a
   link (`get_credentials_link`) and open it themselves to enter them.
3. Every subsequent `jira_*`/`confluence_*` call the assistant makes on
   their behalf (via `mcp_call_tool`) uses their own identity against the
   real Jira/Confluence instance -- not a shared service account.

### Design notes for future maintainers

- No secret is ever a Studio-visible, workspace-shared "provider
  credential" -- the only provider credential this plugin has
  (`credentials_endpoint_url`) is a URL, not a secret.
- Every per-user secret is encrypted at rest with a root secret this
  plugin generates for itself on first use and keeps in its own
  persistent KV storage (`utils/secret_store.py`) -- never admin-entered,
  never in `.env`.
- `self.session.storage.get()` raises rather than returning `None` for a
  missing key (contrary to Dify's own public docs) -- confirmed by reading
  `dify_plugin/invocations/storage.py` directly inside a running
  `plugin_daemon` container. `utils/storage_kv.py` is the one place that
  translates "missing" and "backend error" into `None` so every other
  module doesn't need its own try/except.
- `utils/mcp_client.py` is a minimal, hand-rolled JSON-RPC-over-HTTP
  client for the streamable-HTTP MCP transport -- not the official `mcp`
  SDK, whose client is built around a long-lived async session that
  doesn't fit "reconnect fresh, with this one call's own headers, every
  single invocation." This mirrors the same approach the community's
  `dify-plugin-tools-mcp_sse` plugin uses, trimmed to streamable-HTTP only
  and with per-call (not per-client) headers.
- `config/toolsets.json` is the one file to edit to add a third toolset or
  change a field -- nothing under `tools/`, `endpoints/`, or `utils/`
  hardcodes Jira/Confluence-specific env var names outside of it.
