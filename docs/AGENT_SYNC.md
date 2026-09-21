# Declarative agent management

LibreChat keeps agents in its database, and nothing in `config/librechat.yaml`
defines them. `make agent-export` and `make agent-import` mirror them to and
from files in `agents/`, so they can be reviewed, diffed, versioned and
restored like any other config.

```bash
make agent-export               # database -> agents/*.yaml
make agent-import DRY_RUN=1     # preview what an import would change
make agent-import               # agents/*.yaml -> database
```

Both need the stack running (`make up`). They run
[`scripts/agent-sync.js`](../scripts/agent-sync.js) inside the `api`
container, using LibreChat's own models and permission methods, so nothing
here writes to raw collections or needs an admin token.

## What a file contains

One YAML file per agent (`agents/plan.yaml`):

```yaml
version: 1
agent:
  id: agent_axF0g2A5cWh8jP70LeTI0     # kept as-is: references below depend on it
  name: Plan
  provider: OpenAI Compatible
  model: claude-sonnet-4-6
  instructions: |-
    ...
  tools: [ ... ]
  edges:                              # handoffs
    - { from: agent_axF0..., to: agent_zDn3..., edgeType: handoff }
  subagents:                          # delegation
    enabled: true
    agent_ids: [ agent_uw7B..., agent_n1DK... ]
  # ...every other field of the agent document
access:                               # who else can use it (no owner: see below)
  grants:
    - principal: { type: role, name: USER }
      role: agent_viewer
    - principal: { type: public }
      role: agent_viewer
```

- **`agent`** is the stored document, field for field, minus what belongs to
  the deployment rather than the agent: `_id`, `__v`, timestamps, `tenantId`,
  `versions` (history, rebuilt by LibreChat on every change), `author`
  (the owner, inferred on import) and `mcpServerNames` (derived from `tools`).
- **Agent IDs are preserved**, so handoff `edges`, `subagents` and `agent_ids`
  keep pointing at the right agents in any database you import into, and
  `modelSpecs` in `librechat.yaml` can reference an agent by a stable ID.
- **Sharing is exported by name, not by database ID**, so it survives a fresh
  database. The owner is left out on purpose: it is a person, and people
  differ between deployments (see "Who owns an imported agent"). A principal is `user` (by `email`), `group` (by `name` + `source`),
  `role` (by role name, e.g. `USER`) or `public`. A grant's `role` is
  `agent_viewer`, `agent_editor` or `agent_owner`; an entry that matches none
  of them is kept as raw `permBits`. An optional `expiresAt` is preserved.

## Writing an agent by hand

Exporting first is optional. You can write the files yourself and import them
into any running LibreChat: [`agents/base-agent-template.yaml.example`](../agents/base-agent-template.yaml.example)
is a commented template (copy it, drop `.example`, edit), and this is the smallest valid file:

```yaml
agent:
  id: agent_product-planner
  name: Product Planner
  provider: OpenAI Compatible
  model: claude-sonnet-4-6
  instructions: |
    You are a product planning partner.
```

- **`id`, `name`, `provider` and `model` are required.** Pick a readable
  `id` (`agent_` plus letters, digits, `-` or `_`) and keep it: it is the
  agent's identity, and handoffs, `subagents` and `modelSpecs` refer to it.
  Changing `name` renames the agent; changing `id` creates a new one.
- **Everything else is optional.** What you leave out is what LibreChat would
  have used (`category: general`, no tools, and so on). Running the import
  again changes nothing.
- **Mistakes are caught, not guessed at.** A misspelt field
  (`instruction:`), a missing required field or a value of the wrong type
  skips that one agent with a reason, and everything else is still imported.
  Preview with `DRY_RUN=1`: it checks the same things. (A `provider` or `model`
  that doesn't exist on the instance is handled differently: see below.)
- **Handoff and delegation targets must exist**, either as another file or
  already in the database. A target that doesn't is reported (the agent is
  still applied).
- **Tool names are not checked.** They're stored as written, so a typo
  shows up when the agent runs. See the template for the naming.
- **There is no delete.** Removing a file leaves the agent in the database;
  delete it in LibreChat.

## Import semantics

- **The file is the source of truth for the agents it lists.** A field
  removed from a file goes back to LibreChat's default for it (or is removed,
  if it has none), and a grant not listed under `access.grants` is revoked. Edits made in the UI to a file-managed agent are
  overwritten by the next import, so choose one place to edit.
- **Agents with no file are never touched or deleted.**
- **A file with no `access:` section leaves that agent's sharing alone.** A new
  agent with no `access:` section is shared with nobody but its owner, like one
  created in the UI. `grants: []` means the same, and, for an existing agent,
  revokes every other grant.
- **Idempotent.** Running it again with nothing changed writes nothing.
  Changed agents go through LibreChat's normal update path, so version history
  in the agent builder keeps working.
- **`DRY_RUN=1` writes nothing** and prints the same plan.
- Exit code is `1` if anything couldn't be applied exactly, with the reasons
  listed at the end (grouped, so one missing user is one line, not one per file).
  Everything that could be applied still is. Reasons include:
  - a grant to a user, group or role that doesn't exist;
  - a handoff or subagent pointing at an agent that has no file and isn't in the database;
  - no account that could own a new agent (see below).

## Who owns an imported agent

Files don't name an owner, so an import restored onto any deployment works
without editing them. The owner is the first of these that applies:

1. **`OWNER_EMAIL`**, if you pass it: `make agent-import OWNER_EMAIL=you@company.com`.
   It applies to every agent in the run, including ones already in the
   database (they are re-owned, and the previous owner's owner grant is
   revoked). The account must exist, otherwise the import stops before
   writing anything.
2. **The agent's current owner**, for an agent already in the database. Without
   `OWNER_EMAIL`, an import never re-owns an existing agent.
3. **`ADMIN_EMAIL` from your `.env`**, if that account exists on this instance.
4. **The oldest `ADMIN` account.**

If none of these exist, new agents are skipped and reported: run `make up`
first, which creates the admin. The import prints which owner it is using
before it starts, and the owner always ends up with `agent_owner` on the
agent. None of this is a problem, so it doesn't make the exit code non-zero.

Files exported by an earlier version carry an `access.owner` email and the
owner's own grant. Those still import: a named owner that exists is used
(after `OWNER_EMAIL`), one that doesn't is ignored.

**Sharing stays at the level you set it.** What an agent is shared with
(`role` principals such as `ADMIN` or `USER`, `public`, groups) is exported and
imported unchanged. Only a grant to a *specific person* names an email; if
that person doesn't exist in the target, that one grant is reported and
skipped, and the rest still apply.

## Provider and model

The provider and model in a file may not exist on the instance you import into
(the file came from another deployment, or names a model you don't serve).
The import doesn't skip the agent over that. It falls back, says so, and leaves
the rest to you in the LibreChat UI.

The first rule that applies wins, for the provider and the model separately:

1. **`MODEL_PROVIDER` / `MODEL_NAME`**, if set, are used for every agent,
   existing or new, instead of what the files say. Use these to point a whole
   set of agents at this deployment's model **without editing the files**.
   Values LibreChat wouldn't accept (a provider not in
   `endpoints.agents.allowedProviders`, say) are applied anyway, with a warning:
   you asked for them.
2. **The file's own values**, when they work here: the provider is allowed
   (`endpoints.agents.allowedProviders`) and known to LibreChat (a built-in
   provider or a custom endpoint in `librechat.yaml`), and the model is one
   that provider lists.
3. **Otherwise**, a **new** agent is created with this instance's default, the
   first allowed/defined custom endpoint and its first listed model. An agent
   **already in the database keeps the provider and model it has**, so what you
   then pick in the UI is never overwritten by a file value that doesn't work
   here.

Every fallback is listed at the end of the run, under **"Review in the
LibreChat UI (Agent Builder -> Model)"**, with what the file asked for and what
the agent got. It doesn't change the exit code. Check those agents and choose
the model you want.

Things to know:

- **What "works here" means** is read from this deployment's
  `config/librechat.yaml` only. The model can only be checked for a custom
  endpoint that lists `models.default` and has `fetch: false`, as in this
  stack; for a built-in provider or a fetched list, only the provider is
  checked.
- **No default available** (no custom endpoint listing a model): a new agent
  whose provider doesn't work is skipped with a reason, and an existing one is
  left as it is.
- **An override sticks to agents whose file values don't work.** If you run
  once with `MODEL_NAME=x` and later without it, an agent whose file model
  isn't valid here stays on `x`, per the third rule. Agents whose file values
  are valid go back to the file.
- **Don't export from a deployment you've imported with an override** and commit
  the result: the files would then carry that deployment's model.

## Name guard

An agent's `id` is its identity, so importing a file whose `id` belongs to an
agent already in the database **overwrites that agent**. If the file's `name`
differs from the existing agent's, that looks like two unrelated agents that
picked the same id, not an edit, so the file is skipped with a message and the
run exits `1`:

    id agent_planner belongs to the existing agent "Planner", but this file
    calls it "Somebody Elses Agent". If it is the same agent being renamed,
    re-run with ALLOW_RENAME=1; otherwise give this file its own id

For a real rename, run `make agent-import ALLOW_RENAME=1`. It is meant as a
per-run flag, so it isn't read from `.env`. Choose distinctive ids
(`agent_acme-product-planner`, not `agent_planner`) to avoid clashes in the first
place.

## Options

All optional. `make agent-import DRY_RUN=1 MODEL_NAME=...` on the command line, or
(except `DRY_RUN` and `ALLOW_RENAME`) in `.env`, which is gitignored, so the
agent files can stay exactly as committed. A value on the command line wins
over `.env`.

| Option | Effect |
|---|---|
| `DRY_RUN=1` | Print what would change; write nothing. |
| `OWNER_EMAIL=you@x.com` | Own every agent as this account. See "Who owns an imported agent". |
| `MODEL_PROVIDER=...` | Use this provider for every agent instead of the file's. |
| `MODEL_NAME=...` | Use this model for every agent instead of the file's. |
| `ALLOW_RENAME=1` | Let a file change the name of an agent that already exists. |

## Common workflows

**Edit in the UI, then record it:** change the agent in LibreChat, run
`make agent-export`, review `git diff agents/`, commit. The export replaces the
`*.yaml` files in `agents/` with the database's current state, and removes the
file of an agent that has been deleted.

**Edit in git, then apply:** change the YAML, `make agent-import DRY_RUN=1`,
then `make agent-import`.

**Restore or clone onto another deployment:** `make up`, create the users and
groups the files refer to (`make user-create`, the Admin Panel), then
`make agent-import`. Users, groups and roles are matched by
email/name, so their database IDs can differ; agent IDs stay the same.

## Not covered

- **Agent Actions and attached files** live in their own collections and are
  not exported. The export warns when an agent has any; they need re-creating
  on a new deployment.
- **Fields from a different LibreChat version.** Only fields the running
  version's agent schema knows are written (Mongoose would silently drop the
  rest). A file from a newer LibreChat imports fine into an older one, with a
  `note:` listing what was skipped, and the skipped fields are left alone if the
  database already has them.
- **Role permissions** (whether the `USER` role may use/create/share agents) are
  set in the Admin Panel or `interface.agents` in `librechat.yaml`, not here.
- **Groups and users are never created** by an import.

## Before you commit `agents/`

The files contain each agent's full instructions and the email addresses of
the users it's shared with. Read them before committing or publishing this
repository, and keep secrets out of instructions in the first place: shared
agents can be asked to repeat their prompt.
