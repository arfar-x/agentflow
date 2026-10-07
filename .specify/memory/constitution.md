<!--
Sync Impact Report
- Version: 1.0.0 → 2.0.0 (MAJOR: principles I, III, IV and V redefined; one constraint replaced)
- Modified principles:
  - I. Configuration over code: the application module is `knowledge-ingest/` (with `kb/` until it
    is retired), and the module may consist of plugins for an upstream catalog.
  - III. Hexagonal, provable core: applies to `knowledge-ingest/` and, until retired, `kb/`.
  - IV. Secrets stay in the environment; internal services stay internal: operator UIs may
    publish a port bound to 127.0.0.1. A service may keep its internals on its own private
    network. MCP servers still publish nothing.
  - V. Least privilege per component: catalog reads on the agent path use each user's own
    identity, and ingestion uses a dedicated least-privilege service identity.
- Modified sections:
  - Additional Constraints: "No new infrastructure by default" → "New infrastructure is
    justified". "Declarative state" names source recipes and backed-up UI curation.
- Removed sections: none.
- Templates: `.specify/templates/*` need no change (the constitution check is generic).
- Follow-up:
  - `specs/002-knowledge-datahub/plan.md`: the Constitution Check is re-run against 2.0.0.
  - `AGENTS.md`: updated when `knowledge-ingest/` lands.
  - A PATCH (2.0.1) drops the transitional `kb/` wording once `kb/` is retired.
-->

# agentflow Constitution

## Core Principles

### I. Configuration over code

agentflow is an infrastructure repository: a pinned `docker-compose.yml`, configuration templates
and scripts around LibreChat, a self-hosted OpenAI-compatible model endpoint, MCP servers, and the
knowledge catalog.

- **Upstream before forks.** A need that LibreChat or another upstream component could meet MUST
  be met here by configuration, or built generically upstream (a LibreChat contribution, an
  `agent-skills` release). It MUST NOT be met by patching an upstream component inside this
  repository.
- **One application module.** `knowledge-ingest/` is this repository's own application module.
  Until it is retired, `kb/` is too.
  - Code here extends an upstream component only through that component's supported extension
    points: plugins, transformers, configuration files.
- **No named specifics.** Nothing tracked MAY name a specific model, organization or person. Such
  details live in `.env` files and the generated configuration.
- **Generated files** (`config/librechat.yaml`, `searxng/settings.yml`) MUST NOT be edited by
  hand. Edit their tracked templates.

Rationale: every line of forked behavior is a line that breaks on the next upstream upgrade.

### II. The spec of record leads

`docs/spec/` holds the specification of record for anything large enough to design before
building.

- **Same change.** A behavior change MUST land in the spec in the same change as the code, never
  after it.
- **Ids and status.** Every requirement MUST carry a stable id and a status.
- **Tests claim requirements.** A requirement marked `done` MUST have a test that claims it
  (`# Covers: <id>`). The spec-coverage test enforces this pairing and MUST stay green.
- **Spec Kit directories** (`specs/NNN-*/`) are working documents for one change. Whatever they
  decide about the knowledge catalog MUST also be written into `docs/spec/knowledge-base.md` before
  the change is complete.

Rationale: the code follows the spec. When the two disagree, one of them is wrong, and only a
written spec makes that visible in review.

### III. Hexagonal, provable core (NON-NEGOTIABLE for the application module)

This applies to `knowledge-ingest/`, and to `kb/` until it is retired.

- **Layers.** The module keeps `domain/` → `application/` → `adapters/`.
- **No I/O inside.** `domain/` and `application/` MUST NOT import anything that does I/O: a
  database driver, HTTP client, MCP framework, YAML parser, upstream SDK (for example `datahub`)
  or standard-library I/O module. A boundary test enforces this.
- **Provable with fakes.** Use cases MUST be provable with in-memory fakes, without a database,
  network or model.
- **Typed boundaries.** Data crossing a boundary MUST be validated by a typed model at the
  crossing.
- **Tests with behavior.** New behavior arrives with tests:
  - unit tests run with no services;
  - anything touching a real store or service is also covered by the module's integration target.

Rationale: the catalog's rules have to be checkable in under a second, on a laptop, with no
infrastructure.

### IV. Secrets stay in the environment; internal services stay internal

- **Secrets.**
  - Every secret lives in a gitignored environment file (`.env`, `.env.<service>`) or in
    `searxng/settings.yml`. Tracked files hold `${VAR}` references and placeholder values only.
  - A secret an upstream service must resolve at run time (for example, ingestion credentials) MAY
    be loaded into that service's own secret store from those files. It MUST NOT be written
    anywhere tracked.
- **Published ports.**
  - `api` is the only service with a port published beyond the host.
  - An **operator-facing UI or receiver** MAY publish a port bound to `127.0.0.1` only. Examples
    are the catalog UI and the optional webhook receiver. Exposing one more widely is a
    deployment decision made outside this repository, for example behind company ingress.
  - **MCP servers** MUST NOT publish any port.
- **Networks.**
  - Services `api` calls (MCP servers, `searxng`, and the catalog's API) stay on the `backend`
    network.
  - A multi-service component MAY keep internals that `api` never calls on its own private
    network. Examples are a catalog's database, search engine and event bus.
  - `crw` stays on its own `scraper` network.
- **Coupled keys.** Encryption and signing keys are coupled to the data they protect:
  `CREDS_KEY`, `CREDS_IV`, the JWT secrets, and the catalog's secret-encryption and token-signing
  keys. Changes to backup or restore MUST keep each key with its data.

Rationale: MCP's HTTP transport has no authentication of its own, and a leaked environment value is
the most likely way this stack gets compromised.

### V. Least privilege per component

Each component holds only what its job needs.

- **Agent path.** Catalog reads use **each user's own identity**:
  - a per-user token supplied through LibreChat, so the catalog's policies decide what each user
    sees;
  - no shared credential on the agent path;
  - the agent-facing MCP server runs read-only.
- **Ingestion** runs under a **dedicated service identity**. It holds only what ingestion needs
  (source read credentials, and create/edit of catalog documents), and no user's credentials.
- **Source systems** enforce their own permissions when a user reads through their own
  credentials.
- **Shared text.** Document text the catalog keeps is shared with whoever its policies allow. It
  MUST only be kept for sources an operator opted into, per source, in tracked configuration.
- **Writes to external systems** go through both approval layers: LibreChat `toolApproval`, and
  the toolset's own `--confirm`. Neither layer is removed because the other exists.

Rationale: a component that can't reach a credential can't leak it, and a sharing decision
written in tracked configuration is reviewable.

## Additional Constraints

- **New infrastructure is justified.**
  - The default deployment is one host with one self-hosted OpenAI-compatible model endpoint.
  - A change that adds infrastructure MUST justify it in its plan: what it enables, what simpler
    option was rejected, and how it is pinned and backed up. Examples of added infrastructure are
    services, a search engine, an embeddings model, or a dependency on infrastructure outside this
    repository.
  - Optional capabilities that need more, such as embedding search, are off by default and enabled
    by configuration.
- **Bilingual.** User-facing knowledge is in Persian and English. Language-keyed data stays
  language-keyed, not fixed to two languages.
- **Pinned versions.**
  - Images are pinned by tag in `.env` or `.env.<service>`.
  - `agent-skills` is a submodule pinned to a released tag.
  - Upgrades are reviewable, one-purpose commits with a runbook in `docs/OPERATIONS.md` or the
    service's own `docs/`.
- **Declarative state.**
  - Tracked files are the source of truth for what they list: agents (`agents/*.yaml`), knowledge
    sources (the catalog's tracked source recipes) and configuration templates.
  - Anything derived MUST be rebuildable from them: catalog indexes and generated configs.
  - State that people create in a UI and that no file declares MUST be included in backups. Examples
    are catalog curation, access policies and accounts.

## Development Workflow

- **Branches.** Work happens on a branch cut from the current `main`, never on `main` itself.
- **Commits.** Each commit is one logical step and is described by its message: Conventional
  Commits, with the scope naming the area (`kb`, `knowledge-ingest`, `datahub`, `librechat`,
  `spec-kit`, `readme`).
- **Spec Kit order.** For a change big enough to design:
  1. `/speckit-specify`
  2. `/speckit-clarify` (when anything is ambiguous)
  3. `/speckit-plan`
  4. `/speckit-tasks`
  5. `/speckit-analyze`
  6. `/speckit-implement`

  Each artifact is committed on its own.
- **Gates before a PR:**
  - the application module's unit tests (spec coverage and boundaries included) for any change to
    it;
  - its integration target when storage or an upstream integration changes;
  - config changes rendered and validated against the pinned LibreChat image before they ship.
- **Docs.** Operator-visible behavior is documented in the same change:
  - `docs/CONFIGURATION.md` (or the service's `.env.<service>.example`) for variables;
  - the catalog's operator guide for the catalog;
  - `docs/OPERATIONS.md` or the service's own `docs/` for runbooks.
- **Merging.** A pull request is opened, not merged, by whoever wrote it. Merging is a human
  review decision.

## Governance

- This constitution governs how changes to agentflow are designed and reviewed.
- `AGENTS.md` (with `CLAUDE.md` as a symlink to it) remains the runtime guide for agents working
  in the repository. Where the two disagree, this document wins until one of them is amended.
- An amendment is a reviewed pull request that edits this file and states its version bump:
  - MAJOR when a principle is removed or redefined;
  - MINOR when a principle or section is added or materially expanded;
  - PATCH for wording.
- Plans produced by `/speckit-plan` MUST include a constitution check. A violation is either
  removed from the plan or recorded with its justification. A recorded violation of a MUST is not
  a substitute for an amendment: the amendment lands before the work that needs it.

**Version**: 2.0.0 | **Ratified**: 2026-10-04 | **Last Amended**: 2026-10-07
