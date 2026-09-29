# Figma

Agents can read Figma designs: paste a Figma link into a conversation and the
agent gets the frame's layout, styles, text, and components as structured
data, enough to describe a screen, check it against a PRD, or draft the code
for it. This comes from the `mcp-figma` service: Framelink's community MCP
server ([`figma-developer-mcp`](https://github.com/GLips/Figma-Context-MCP),
pinned in [`figma-mcp/Dockerfile`](../figma-mcp/Dockerfile)), which works
through Figma's public REST API. It is **not** Figma's own MCP server; the
section below on the official server explains why.

## What users get

One read-only tool, `get_figma_data`. In LibreChat it's named
`get_figma_data_mcp_figma`, which is the name to use in an agent's `tools:`
list. It takes a Figma file key and, optionally, a node id, both of which the
model reads from a pasted link
(`figma.com/design/<fileKey>/...?node-id=<nodeId>`). It returns a simplified
tree of that node: layout (auto layout, sizing, spacing), fills, strokes,
effects, text and its styles, and component and instance names.

What it doesn't do:

- **No image export.** Framelink's other tool, `download_figma_images`, writes
  files into the server's own filesystem, which no user or agent can read in
  this stack. `docker-compose.yml` turns it off (`SKIP_IMAGE_DOWNLOADS`).
- **No writing to Figma, and none of the official server's extras**: Code
  Connect mappings, variable definitions, screenshots, and
  `get_design_context`'s generated code all exist only on Figma's own server.

## Who can use it: Figma's rate limits

Each call is one Figma REST request (`GET /v1/files/:key` or `.../nodes`),
made as the user whose token it is. These are Figma's "Tier 1" endpoints, and
their limit depends on that user's seat and on the plan the file lives in:

- **Dev or Full seat on a Professional, Organization, or Enterprise plan**:
  a per-minute limit. That's fine for chat use.
- **View or Collab seat, or any file in a Starter (free) plan**: **up to 6
  requests per month**. That is a monthly quota, not a per-minute one.

This isn't a limitation of this route. Figma's own MCP server caps the same
seats at 6 tool calls a month too. Once a user's quota is spent, their calls
fail with `429` until it resets. See Figma's
[rate limits](https://developers.figma.com/docs/rest-api/rate-limits).

## Setup

**Operators:** nothing to configure. `make up` builds and starts
`mcp-figma` like every other service. It needs no `.env` variables and has no
credential of its own. On a deployment that predates it, run `make up` again:
that re-renders `config/librechat.yaml` and builds the new service.

**Each user**, once:

1. In Figma, create a personal access token (Settings > Security > Personal
   access tokens) with the **File content: Read** and **Dev resources: Read**
   scopes. See Figma's
   [guide](https://help.figma.com/hc/en-us/articles/8085703771159-Manage-personal-access-tokens).
2. In LibreChat, open the MCP servers panel, find **figma**, paste the token
   into "Figma personal access token", and save. This is the same form each
   user fills in with their own Jira and Confluence credentials.

To give an agent the tool, add it in the Agent Builder, or in the agent's file
under `agents/`:

```yaml
  tools:
    - get_figma_data_mcp_figma
```

Then run `make agent-import`. Without an agent, a user can also pick **figma**
from the chat's MCP menu.

## How it's wired

```
LibreChat api ──X-Figma-Token: <that user's token>──▶ mcp-figma:3333/mcp ──▶ https://api.figma.com/v1
               (backend network, no published port)                         (as that user)
```

- **Per-user, with no shared fallback.** LibreChat sends each user's own token
  on every request (`customUserVars` → the `X-Figma-Token` header, in
  `config/librechat.yaml.example`). `mcp-figma` builds a fresh server for each
  request with that token and keeps nothing afterwards. It has no token of its
  own, so a user who hasn't saved one can read nothing at all. Each user sees
  exactly the files their own Figma account can open.
- **Same isolation as `mcp-agent-skills`**: no published port, `backend`
  network only, and LibreChat's `mcpSettings.allowedAddresses` exempts just
  `mcp-figma:3333` from its SSRF block. Never add a `ports:` entry to it.
- **No `env_file`**, unlike `mcp-agent-skills`. This is third-party code, and
  none of this stack's secrets are any of its business.
- **Telemetry off.** Framelink sends usage telemetry to a third party by
  default. `FRAMELINK_TELEMETRY=off` and `DO_NOT_TRACK=1` in
  `docker-compose.yml` disable it, and the startup log says
  `TELEMETRY: disabled`.
- **It needs outbound HTTPS to `api.figma.com`.** Behind a corporate proxy,
  add `HTTPS_PROXY` (and `NO_PROXY`) or Framelink's own `FIGMA_PROXY` to the
  service's `environment:`.

## Why not Figma's official MCP server

Figma ships two MCP servers of its own, and neither fits a shared,
self-hosted LibreChat:

- **The remote server, `https://mcp.figma.com/mcp`, rejects LibreChat.** It
  only accepts OAuth: no personal access tokens, no service accounts, and its
  `mcp:connect` scope can't be granted to an ordinary Figma OAuth app. Its
  dynamic client registration endpoint
  (`POST https://api.figma.com/v1/oauth/mcp/register`) answers `403` unless
  the registering `client_name` is on the allowlist behind Figma's
  [MCP catalog](https://www.figma.com/mcp-catalog/) (Claude Code, Cursor,
  VS Code, Codex, and so on). LibreChat v0.8.7 always registers as
  `client_name: 'LibreChat MCP Client'` (hard-coded in
  `packages/api/src/mcp/oauth/handler.ts`), which isn't on the list. So the
  connection fails at registration, before any Figma login page ever appears.
  Every other client that isn't in the catalog hits the same wall (see
  [geelen/mcp-remote#186](https://github.com/geelen/mcp-remote/issues/186)).
  LibreChat users have asked Figma to add it; until Figma does, no
  configuration change on this side can make this work.
- **The desktop server, `http://127.0.0.1:3845/mcp`,** runs inside the Figma
  desktop app on one person's machine. It listens on that machine's localhost
  only, needs a Dev or Full seat, and answers for whatever that one desktop
  app has open. That makes it one designer's tool, not a service for a
  multi-user stack.

Verified by reading the code and docs, not against Figma itself: the network
this was built from can't reach any figma.com host. `mcp-figma` itself was
built and run on the stack's `backend` network and reached by service name.
It passed an MCP handshake and a tool listing using the same MCP SDK client
LibreChat uses, and its tool call got as far as the request to
`api.figma.com`. `config/librechat.yaml.example` validates against LibreChat
v0.8.7's own config schema.

## Other routes, and when to switch

- **Figma's remote server, the sanctioned way.** If Figma adds LibreChat to
  its catalog, or your Figma account team issues a pre-registered OAuth client
  for this deployment, the switch is config only. LibreChat accepts a
  pre-registered client in an `mcpServers` entry: `url:
  https://mcp.figma.com/mcp` plus an `oauth:` block with `client_id` (and
  `client_secret`, `authorization_url`, `token_url` if Figma issues a secret).
  Each user then signs in to Figma from LibreChat and gets Figma's own tool
  set, including writing to the canvas. Nothing here has been tested against
  it, because nobody outside the allowlist can test it.
- **Bridging one designer's desktop server.** An SSH reverse tunnel can carry
  someone's `127.0.0.1:3845` to the Docker host, where LibreChat could reach
  it. It has been reported working with other clients, but not tried here.
  Every LibreChat user who can reach it would then act as that one designer,
  in whatever file their desktop app has open. That rules it out for a shared
  stack.
- **Registering under an allowlisted client's name**, for example the
  `client_name` of a catalog client (which is what `pi-figma-remote` does).
  Community reports say this currently gets past the `403`. It's deliberately
  not used here. It misrepresents who the client is, to Figma and on every
  user's consent screen. It is plausibly against Figma's Developer Terms. And
  Figma can close it at any moment, without notice, taking every user's
  connection down at once.
- **Writing to Figma.** Community "talk to Figma" bridges drive a Figma plugin
  over a WebSocket relay. They need each user's desktop app running with the
  plugin open, so they can't be a server-side service.

## Upgrading

The package version is the `FIGMA_DEVELOPER_MCP_VERSION` build arg in
[`figma-mcp/Dockerfile`](../figma-mcp/Dockerfile), pinned the same way as the
`agent-skills` submodule. Bump it as a reviewable one-line commit, then:

```bash
make build SERVICE=mcp-figma
```

Read Framelink's
[CHANGELOG](https://github.com/GLips/Figma-Context-MCP/blob/main/CHANGELOG.md)
first. A renamed tool silently drops out of every agent whose `tools:` list
still carries the old name. Confirm the tool list afterwards with the command
in [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) "Figma".
