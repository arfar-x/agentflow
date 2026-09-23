# Agent instructions: `kb/`

The knowledge catalog: what knowledge exists in the organization and where it
lives. This file governs work inside `kb/`; repo-wide rules are in the root
[`AGENTS.md`](../AGENTS.md), and [`README.md`](README.md) here covers running it
and a REPL walkthrough.

**[`docs/spec/knowledge-base.md`](../docs/spec/knowledge-base.md) is the source
of truth for behavior.** Read it before changing anything. It is not background
reading: requirements carry ids, tests claim them, and the suite fails when the
two disagree.

## Spec-first workflow

Behavior changes in the spec first, then in code:

1. Add or amend the requirement row in the spec (`FR-…` / `NFR-…`), status
   `planned`.
2. Write the test, with a `# Covers: FR-ENT-02, FR-REC-05` line naming the
   requirement(s) it proves.
3. Implement it.
4. Flip the status to `done` in the same change.

Skipping any step fails `tests/test_spec_coverage.py`: a `done` requirement with
no test, a test claiming an id the spec doesn't define, or a `planned`
requirement that quietly has tests are all build failures.

## Setup and testing

```bash
cd kb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
python -m pytest                      # whole suite, no services needed
python -m pytest tests/domain         # the pure rules only
```

That run needs no database, network or model, and takes well under a second.
Keep it that way — a test that needs a service belongs in `tests/integration/`
and must skip when `KB_TEST_DATABASE_URL` is unset.

The Postgres-backed tests run against a throwaway container:

```bash
make kb-test                    # from the repo root; cleans up after itself
```

The root `make test` still runs only `agent-skills`' suite.

## Layout and the dependency rule

```
src/kb/
├── domain/        pure rules: entry, text, identity, ranking, merge, policies
├── application/   ports/ (protocols) + use_cases/
└── adapters/
    └── outbound/  postgres_store.py (the only adapter so far)
migrations/        SQL applied by `make kb-init`; append-only -- a shipped file
                   is never edited, a change is a new numbered file
```

Dependencies point inward only: `adapters → application → domain`.

- `domain/` and `application/` MUST NOT import psycopg, requests, fastmcp, yaml,
  or a stdlib I/O module. `tests/test_boundaries.py` walks the AST and fails if
  they do.
- pydantic is allowed inside — it is validation, not I/O.
- `domain/` must not import from `application/`.
- Everything that talks to the outside world is a port implementation in
  `adapters/`, constructed only in the composition root.

## Conventions

- **Models over dicts at every boundary.** `Entry`, `Location`, `Override`,
  `SourceDocument`, `SummaryDraft` are frozen pydantic models with
  `extra="forbid"`. Data arriving from a source, a model draft, a database row
  or a JSON payload is validated where it crosses, not trusted.
- **An invalid entry must stay impossible to construct.** Validation lives in
  `Entry`'s own validators, not in the caller. In particular an entry whose
  document could never be fetched is rejected — "findable but unreadable" is the
  failure this module exists to prevent.
- **One funnel for writes.** Every freshness mechanism goes through
  `use_cases/reconcile_document.py`. Don't build an `Entry` anywhere else in
  `application/`; a second path is how four mechanisms drift into four
  definitions of an entry.
- **No model on the query path.** `SearchCatalog` takes a store and a clock and
  nothing else, so search keeps working when the model endpoint is down.
  Summarization happens during sync only.
- **Normalize both sides.** Any text compared against stored text goes through
  `domain/text.normalize`. Changing it changes stored data, so it needs a
  reindex — say so in the change.
- **Comments explain why, not what.** The non-obvious calls (ZWNJ folding to a
  space, the version being part of the content hash, ties broken by first
  appearance) carry their reasoning inline. Match that density.

## Adding things

- **A source** is one file in `adapters/outbound/` implementing
  `KnowledgeSource`, plus one entry in `config/kb-sources.yaml`. If `domain/` or
  `application/` needs to change, the port is wrong — fix the port, not the
  layer.
- **A use case** depends on ports only, and gets tested against the in-memory
  fakes in `tests/application/fakes.py`. Extend those fakes rather than reaching
  for a real adapter in a test.
- **A new port** goes in `application/ports/` as a `Protocol`, and its fake must
  satisfy it (`tests/application/test_ports_models.py` asserts this with
  `isinstance`).

## Front doors

- **The CLI prints exactly one JSON document and exits 0 for anything handled**,
  errors included -- the same contract `agent-skills`' toolsets follow, so
  scripts never have to tell "the tool failed" from "the tool reported a
  failure". A genuine bug still raises.
- **An MCP tool returns a structured error rather than raising.** An exception
  fails the agent's whole turn over a search that was only meant to add context.
- **fastmcp takes only a docstring's summary line as a tool's description**, so
  anything a model needs in order to call a tool correctly is passed explicitly
  as `description=`. Per-argument help does come from the docstring's `Args:`
  section. A test asserts both, because this is silent when it regresses.
- **Nothing a model can call may write.** Writes belong to the CLI.

## Sources

- **A source adapter maps, it does not decide.** It turns whatever an API
  returns into `SourceDocument`s; what happens to them is `reconcile_document`'s
  business, and the cost rules live there.
- **A bad document is skipped and reported, never fatal.** One page without an
  id must not end a run over a 900-page space.
- **Credentials are a read-only service account's, read from the environment
  by `source_factory`** -- never from `kb-sources.yaml`, which carries only the
  *name* of the variable. Sync's account is not a user's: an agent reading the
  real page later does so as the user, through agent-skills.
- **The summarizer's output is data.** The document went into a prompt; what
  comes back is parsed, validated and stored, never followed.
- **Interpolation skips comments.** The config file documents `${VAR}` in its
  own header, and resolving that would break the file it explains.

## Storage

- **Migrations are append-only.** A shipped file is never edited; a change is a
  new numbered file. `make kb-init` records what it applied and skips the rest.
- **`entry.data` holds the whole entry as JSONB**, and only the columns
  something filters or sorts on are promoted out of it. The model owns the
  shape, so a new entry field needs no migration.
- **`entry_search` is derived** — the entry merged with its override,
  normalized. Rebuild it rather than patching it (`_rebuild_search`), so there
  is one definition of what is searchable and it lives in the domain.
- **Ranking stops at the adapter.** It returns one ranked list per query;
  fusing several is the domain's job.

## Not built yet

The GitLab, HTTP API and local-file sources, the scheduler and the webhook
receiver are phases 6–9 in [`README.md`](README.md#status). Don't document them
here as if they exist, and don't assume a missing module means something was
deleted.
