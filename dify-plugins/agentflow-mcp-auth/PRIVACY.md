## Privacy Policy

This plugin is internal to the `agentflow-dify` deployment. It is not
published to the Dify Marketplace and is not intended to be installed
anywhere else without reading this file and `README.md` first.

### Data Collection

- **Jira/Confluence credentials** (base URL, username, password/API
  token, default project/space) that a Dify end user submits through this
  plugin's own credentials form (`endpoints/credentials_*.py`), entered
  directly by that user in their own browser -- never typed into chat,
  never seen by the LLM.
- **The Dify end-user id** (`ToolRuntime.user_id`, provided by Dify's own
  backend, not self-asserted by the user) that a credential is stored
  against.

### Data Usage

- A stored credential is used for exactly one purpose: as an
  `X-Agent-Skills-Env-<VAR>` HTTP header on that same user's own calls to
  this deployment's internal `mcp-agent-skills` MCP server, so their
  Jira/Confluence actions run under their own identity rather than a
  shared service account. See `agent-skills/AUTHENTICATION.md` (Part 2)
  in this repo for the receiving end of that mechanism.
- Nothing here is sent to any third party other than the Jira/Confluence
  instance the user themselves configured, via `mcp-agent-skills`.
- `mcp-agent-skills` itself already redacts every field marked
  `sensitive: true` (passwords/tokens) from any error output it returns,
  per its own `AUTHENTICATION.md`; this plugin adds no additional logging
  of credential values on top of that.

### Data Retention

- Each credential is encrypted at rest (`utils/secret_store.py`) and kept
  in this plugin's own persistent storage until the user deletes it
  themselves (the "Clear My Credentials" tool) or an admin uninstalls this
  plugin (which discards its entire storage).
- The signed link this plugin issues (`get_credentials_link`) expires
  after 15 minutes and is single-purpose -- it authorizes filling in one
  toolset's form for one user, nothing else.

### Contact

This is this repository's own internal plugin -- see the repository's
root `README.md`/`AGENTS.md` for who maintains it.
