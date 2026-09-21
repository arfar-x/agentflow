# Agent instructions: `agents/`

Declarative definitions of the LibreChat agents this stack runs: one YAML file
per agent, applied to a running LibreChat with `make agent-import`. This file
governs work inside `agents/`; the repo-wide rules are in the root
[`AGENTS.md`](../AGENTS.md).

**Read [`docs/AGENT_SYNC.md`](../docs/AGENT_SYNC.md) first.** It is the
reference for the file format, what an import does and doesn't touch, how the
owner and the provider/model are chosen, and every option. This file only
covers what to keep in mind while editing here.

## Files

- `*.yaml` / `*.yml` are agents. Nothing else in this directory is read, so this
  file and `base-agent-template.yaml.example` are safe here.
- Start a new agent from
  [`base-agent-template.yaml.example`](base-agent-template.yaml.example): copy
  it, drop `.example`, edit. Only `agent.id`, `name`, `provider` and `model`
  are required.

## Workflow

```bash
make agent-import DRY_RUN=1     # validate and preview; writes nothing
make agent-import               # apply to the running LibreChat
```

Import is idempotent and never deletes: removing a file leaves the agent in
LibreChat.

## Rules

- **The files are the source of truth for the agents they list.** A change made
  to one of these agents in the LibreChat UI is overwritten by the next import.
  To keep a UI change, put it in the file (or `make agent-export`).
- **`agent.id` is the agent's identity, so never change it** and don't reuse one.
  Handoffs (`edges`), delegation (`subagents`) and `modelSpecs` refer to agents
  by id. Changing it creates a new agent and orphans the old one. Pick
  distinctive ids: an id that already exists under a different `name` is
  refused (`ALLOW_RENAME=1` overrides, for a genuine rename).
- **`make agent-export` replaces every `*.yaml` here** with what is in the
  database, discarding uncommitted hand edits, and brings back a file you
  deleted for an agent that still exists in LibreChat. Commit or stash first, and
  never export from a deployment you imported into with `MODEL_PROVIDER` or
  `MODEL_NAME` set, because the files would then carry that deployment's model.
- **Don't edit a file to fit one deployment.** Use `MODEL_PROVIDER`,
  `MODEL_NAME` and `OWNER_EMAIL` (command line or the gitignored `.env`)
  instead, so the committed files stay identical everywhere.
- **No owner in a file, on purpose.** The importer infers it. Sharing is
  `access.grants`, by role, group or `public`. `grants: []` means private to the
  owner, and leaving `access:` out keeps an existing agent's sharing as it is.
- **Tool names are not validated.** They are stored as written and a typo only
  fails when the agent runs. MCP tools are named
  `<toolset>_<action>_mcp_<server>`, as in the `toolApproval.ask` list in
  [`config/librechat.yaml`](../config/librechat.yaml).
- **Don't hand-write** `avatar`, `actions`, `tool_resources`, `skills`, `author`,
  `versions` or timestamps. They point at other LibreChat data or are managed by
  it, and `author`, `versions` and timestamps are rejected.
- **These files are committed, and shared agents can be asked to repeat their
  prompt.** Keep secrets, credentials, personal emails and organisation-specific
  detail out of `instructions`.
