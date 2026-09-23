# Knowledge base (`kb`) — software requirements specification

| | |
|---|---|
| **Spec ID** | SPEC-KB-001 |
| **Status** | Active — partially implemented (see §12) |
| **Version** | 1.5.0 |
| **Last updated** | 2026-09-24 |
| **Implements** | `kb/` module, `mcp-kb` service |
| **Related** | [`AGENTS.md`](../../AGENTS.md), [`docs/CONFIGURATION.md`](../CONFIGURATION.md), [`kb/README.md`](../../kb/README.md) |

Requirement keywords (**MUST**, **MUST NOT**, **SHOULD**, **MAY**) are used as
defined in RFC 2119.

This specification is **executable**. Every requirement carries a stable id;
`kb/tests/test_spec_coverage.py` fails when a requirement marked *done* has no
test claiming it, or when a test claims an id this document does not define.
Behavior changes here first, then in code.

---

## 1. Purpose

Agents in this stack can call Jira and Confluence, but nothing tells them what
knowledge exists in an organization or where it lives. A question phrased in one
team's vocabulary misses a document written in another's, because keyword search
over a wiki finds only literal matches. The agent fills the gap by assuming, and
an assumption about what a product, term or team means is indistinguishable from
a fact to the person reading the answer.

This module builds a **catalog**: a searchable record of what each piece of
knowledge is, what it is about in each language, who owns it, and exactly where
it lives — so an agent follows a real reference to a real document instead of
inventing one.

## 2. Goals and non-goals

**Goals**

- G1. Make organizational knowledge discoverable across the vocabulary and
  language gap between a question and a document.
- G2. Keep every answer traceable to a real document the agent actually read.
- G3. Stay current automatically, with no manual curation step in the loop.
- G4. Run on existing infrastructure, with no embeddings model and no
  dedicated retrieval service.

**Non-goals**

- N1. Not a document store: the catalog never holds document bodies.
- N2. Not an authoring tool: knowledge stays where its authors already write it.
- N3. Not an access-control system: source systems enforce their own
  permissions, and the catalog holds nothing restricted (see §10, C4).

## 3. Glossary

| Term | Meaning |
|---|---|
| **Catalog** | The derived, searchable set of entries. Rebuildable from sources at any time. |
| **Entry** | One record describing one source document: what it is, what it is about, where it lives. |
| **Source** | A configured system the catalog reads: a set of wiki spaces, repository scopes, an issue-tracker query, an HTTP endpoint. |
| **Source document** | One item as its source describes it, before the catalog forms an opinion. |
| **Fetch hint** | The tool name and arguments an agent uses to read the real document. |
| **Override** | A human correction applied on top of a generated entry. |
| **Reconcile** | Compare one source document against the catalog and update it. |
| **Stale** | The catalog's summary may lag the document; the document itself is always read live. |
| **Gap** | A search that matched nothing — evidence of knowledge nobody has written down. |

## 4. Users and acceptance scenarios

**Primary users:** everyone using the chat front door (product, business and
engineering), through an agent. **Secondary user:** the operator who configures
sources and reviews overrides.

| id | Scenario | Status |
|---|---|---|
| AS-01 | **Given** a wiki page titled "Payment reconciliation" describing refund retries, **when** a user asks "how do we handle refund retries?", **then** the agent finds that page and answers from its live content, naming it. | done |
| AS-02 | **Given** a document summarized in English and Persian, **when** a user asks in Persian, **then** the catalog returns it and the agent answers in Persian. | done |
| AS-03 | **Given** nothing in the catalog matches, **when** a user asks, **then** the agent says so and asks, rather than guessing, and the query is recorded as a gap. | partial |
| AS-04 | **Given** a page edited at the source, **when** the next sync runs, **then** the entry reflects the edit without a human touching the catalog. | done |
| AS-05 | **Given** a page deleted at the source, **when** reconciliation runs, **then** the entry stops appearing in search and is reported, not erased. | done |
| AS-06 | **Given** a generated summary an operator disagrees with, **when** they record an override, **then** search returns their text, and it survives every later sync. | done |
| AS-07 | **Given** a new wiki space or repository, **when** the operator runs discovery, **then** it is proposed as a disabled candidate for review, not silently indexed. | done |

*partial* = the domain and application behavior exists and is tested; the
adapter or agent wiring that completes the scenario is not built yet.
*deferred* (§6.8) = specified and deliberately not built, awaiting a real use
case.

## 5. Key entities

| Entity | Fields | Notes |
|---|---|---|
| **Entry** | `id`, `type`, `title{lang}`, `summary{lang}`, `keywords`, `tags`, `aliases`, `owner_team`, `audience`, `status`, `location`, `source_id`, `source_version`, `content_hash`, `created_at`, `updated_at`, `source_created_at`, `source_updated_at`, `last_seen_at`, `deleted_at` | `type` ∈ {doc, spec, product, system, team, person, term} |
| **Location** | `kind`, `ref{}`, `url` | `kind` names the system; `ref` is what that system needs to fetch the document again |
| **Override** | `entry_id`, any entry field, `exclude`, `note` | Stored separately from entries; re-applied after every sync |
| **SourceDocument** | `source_id`, `external_id`, `title`, `body`, `location`, `type`, `version`, timestamps, `tags`, `owner_team` | The input to reconciliation |
| **SummaryDraft** | `title{lang}`, `summary{lang}`, `keywords`, `aliases` | Model output; data, never instructions |
| **Gap** | `query`, `at` | One row per search that matched nothing |

## 6. Functional requirements

### 6.1 Entries — `FR-ENT`

| id | Requirement | Status |
|---|---|---|
| FR-ENT-01 | An entry MUST record what a document is and where it lives, and MUST NOT store the document's body. | done |
| FR-ENT-02 | An entry whose document could never be fetched MUST be rejected when it is built: a known source kind missing its reference key, or an unknown kind with no URL. | done |
| FR-ENT-03 | `title` and `summary` MUST be language-keyed maps, and an entry MUST have non-empty title text in at least one language. | done |
| FR-ENT-04 | Every entry MUST name the source that produced it. | done |
| FR-ENT-05 | Entry ids MUST be deterministic: the same document yields the same id from every mechanism and every run, and two sources MUST NOT collide. | done |
| FR-ENT-06 | Entries MUST be immutable, MUST reject unknown fields, and validation MUST report every problem in one error. | done |
| FR-ENT-07 | Entries MUST round-trip through JSON unchanged. | done |
| FR-ENT-08 | Catalog timestamps MUST be kept separately from the source's own timestamps. | done |
| FR-ENT-09 | An entry MUST expose a fetch hint, or none when no tool in this stack can read that source. | done |
| FR-ENT-10 | Every entry MUST carry an `audience` (default `all`); this version MUST NOT enforce it. | done |

### 6.2 Text matching — `FR-TXT`

| id | Requirement | Status |
|---|---|---|
| FR-TXT-01 | Normalization MUST fold Persian/Arabic letter variants (yeh, kaf, alef forms, teh marbuta), both Arabic-Indic digit ranges, harakat and tatweel. | done |
| FR-TXT-02 | A zero-width non-joiner MUST be treated as a word boundary. | done |
| FR-TXT-03 | Normalization MUST be idempotent and MUST be applied identically when writing and when querying. | done |
| FR-TXT-04 | Punctuation and symbols MUST become boundaries, and Latin text MUST be casefolded. | done |

### 6.3 Search — `FR-SRCH`

| id | Requirement | Status |
|---|---|---|
| FR-SRCH-01 | Search MUST accept several queries in one call and fuse their rankings, so an entry found by two phrasings outranks one found by either. | done |
| FR-SRCH-02 | Fusion MUST be deterministic for a given input. | done |
| FR-SRCH-03 | Search MUST support filtering by entry type and by tag, and a result limit. | done |
| FR-SRCH-04 | Excluded and soft-deleted entries MUST NOT appear, and MUST NOT consume a result slot. | done |
| FR-SRCH-05 | A stale entry MUST be returned and flagged, not hidden. | done |
| FR-SRCH-06 | Search MUST return entries with their overrides applied. | done |
| FR-SRCH-07 | A search matching nothing MUST be recorded as a gap; a query with no searchable terms MUST NOT be. | done |
| FR-SRCH-08 | Queries MUST be de-duplicated and length-bounded before reaching the store. | done |
| FR-SRCH-09 | Search MUST NOT call a model or make a network request. | done |
| FR-SRCH-10 | Matching MUST weight a term found in an entry's title above one in its keywords, above one in its summary, and MUST additionally reward an exact substring match — product names, identifiers and error strings match literally or not at all. | done |

### 6.4 Reconciliation and freshness — `FR-REC`

| id | Requirement | Status |
|---|---|---|
| FR-REC-01 | Every freshness mechanism MUST reconcile through one code path. | done |
| FR-REC-02 | A document whose content is unchanged MUST cost no model call and no write. | done |
| FR-REC-03 | An unchanged document MUST update `last_seen_at` without moving `updated_at`. | done |
| FR-REC-04 | Changed content MUST be re-summarized, and the entry MUST keep its original `created_at`. | done |
| FR-REC-05 | A document that vanishes MUST be soft-deleted and reported, never hard-deleted; one that returns MUST be revived, not duplicated. | done |
| FR-REC-06 | A summarizer returning nothing usable MUST still leave the document catalogued under its real title. | done |
| FR-REC-07 | Renaming a document MUST NOT fork it into a second entry. | done |
| FR-REC-08 | Incremental sync MUST ask each source only what changed since a stored per-source checkpoint, and MUST advance that checkpoint only after a successful run. | done |
| FR-REC-09 | Nightly full reconciliation MUST list every document in a source and soft-delete entries it no longer yields. | done |
| FR-REC-10 | Reading a stale entry MUST queue a refresh of that entry. | planned |
| FR-REC-11 | A webhook MUST refresh only the paths in its payload, and MUST be rejected without a valid secret token. | planned |

### 6.5 Overrides — `FR-OVR`

| id | Requirement | Status |
|---|---|---|
| FR-OVR-01 | Localized fields MUST merge per language. | done |
| FR-OVR-02 | List fields MUST replace wholesale, and an empty list MUST clear the field. | done |
| FR-OVR-03 | An unset scalar MUST mean "no opinion", not "clear it". | done |
| FR-OVR-04 | `exclude` MUST remove an entry from search without changing anything at the source. | done |
| FR-OVR-05 | Applying an override to a different entry MUST be an error, not a silent no-op. | done |
| FR-OVR-06 | An override MUST survive every subsequent sync of its source. | done |

### 6.6 Source configuration — `FR-CFG`

| id | Requirement | Status |
|---|---|---|
| FR-CFG-01 | The source config MUST support `${VAR}` and `${VAR:-default}` anywhere, for any variable, with no predefined set. | done |
| FR-CFG-02 | An unset `${VAR}` MUST fail naming the file, line and variable. | done |
| FR-CFG-03 | Interpolated values MUST be coerced to the type the field expects. | done |
| FR-CFG-04 | Credentials MUST be referenced by variable name only; no secret may appear in the config, and resolved secrets MUST be redacted from logs and errors. | done |
| FR-CFG-05 | Sync MUST refuse to run while the config says `reviewed: false`. | done |
| FR-CFG-06 | Discovery MUST draft the config from what each system contains, writing every candidate disabled, and MUST NOT rewrite a line an operator has edited. | done |
| FR-CFG-07 | Each freshness mechanism MUST be switchable globally and per source, and a disabled mechanism MUST do nothing. | done |

### 6.7 Front doors — `FR-CLI`, `FR-MCP`

The CLI is the operator's door; the MCP server is the agent's. Both are thin:
they parse input, call a use case, and serialize the result.

| id | Requirement | Status |
|---|---|---|
| FR-CLI-01 | The CLI MUST print exactly one JSON document to stdout for every invocation, success or failure alike, and MUST exit 0 for any handled outcome — including a reported error. | done |
| FR-CLI-02 | The CLI MUST expose, at minimum: search, get, override (set and clear), gaps, migrate, and status. | done |
| FR-CLI-03 | A failure the operator can act on (no database, a malformed argument) MUST be reported as a structured error naming what failed, never as a traceback. | done |
| FR-CLI-04 | `status` MUST report what the catalog actually contains — entry counts by source and type, soft-deleted count, override count, the applied migrations, and the recorded gaps — so "is this thing working" is answerable without SQL. | done |
| FR-MCP-01 | The MCP server MUST expose exactly two tools, `kb_search` and `kb_get`, both read-only. | done |
| FR-MCP-02 | Each tool's schema MUST describe its arguments well enough for a model to call it correctly without reading the spec, and `kb_search` MUST accept several query strings in one call. | done |
| FR-MCP-03 | A tool MUST return a structured error rather than raising when the catalog is unreachable, so an agent can say so and carry on instead of failing the turn. | done |
| FR-MCP-04 | The server MUST NOT accept credentials or per-user variables: the catalog is shared, and there is nothing user-specific for a caller to supply. | done |

### 6.8 Sources — `FR-SRC`

*deferred* = specified, deliberately not built. The shape is settled so it costs
nothing to leave here, but building an adapter before a real document needs it
means guessing at a mapping and then maintaining the guess. These get built when
something concrete has to be catalogued through them.

| id | Requirement | Status |
|---|---|---|
| FR-SRC-01 | Confluence: selected spaces, page label to entry type, label exclusions. | done |
| FR-SRC-02 | Jira: documents selected by JQL. | done |
| FR-SRC-03 | GitLab: each project lists scopes; a scope is a directory walked recursively with file patterns and exclusions; `dir: "."` means the whole repository; a scope's `type` and `tags` are inherited by every entry beneath it. | done |
| FR-SRC-04 | An internal HTTP API, with field mapping in config. | deferred |
| FR-SRC-05 | Local files under configured paths. | deferred |
| FR-SRC-06 | Adding a source MUST require only one adapter and one config entry, with no change to `domain/` or `application/`. | planned |

### 6.9 Agent integration — `FR-AGT`

| id | Requirement | Status |
|---|---|---|
| FR-AGT-01 | `kb_search` and `kb_get` MUST be read-only, and therefore MUST NOT require a `toolApproval` entry. | done |
| FR-AGT-02 | The front-door agent MUST search the catalog before answering or delegating, MUST NOT answer from assumption, MUST answer in the user's language, and MUST name the source document it used. | planned |
| FR-AGT-03 | Document-producing agents MUST ground their drafts in catalog results. | planned |

## 7. Non-functional requirements

### 7.1 Architecture — `NFR-ARC`

| id | Requirement | Status |
|---|---|---|
| NFR-ARC-01 | Nothing in `domain/` or `application/` may import a database driver, HTTP client, MCP framework, YAML parser, or standard-library I/O module. | done |
| NFR-ARC-02 | `domain/` MUST NOT depend on `application/`. | done |
| NFR-ARC-03 | Data crossing a boundary MUST be validated by a typed model at the crossing. | done |
| NFR-ARC-04 | Use cases MUST be provable with in-memory fakes, without a database, network or model. | done |

### 7.2 Deployment and operations — `NFR-DEP`

| id | Requirement | Status |
|---|---|---|
| NFR-DEP-01 | The catalog MUST have its own database instance, separate from LibreChat's `vectordb`. | done |
| NFR-DEP-02 | `mcp-kb` MUST have no published port and MUST be reachable only on the internal network. | done |
| NFR-DEP-03 | Scheduled work MUST run in its own service, not inside the MCP server. | planned |
| NFR-DEP-04 | The webhook receiver MUST be a separate component on a private interface. | planned |
| NFR-DEP-05 | The query path MUST use a least-privilege database role. | done |
| NFR-DEP-06 | The index MUST be rebuildable from sources; only the gap log and checkpoints require backup. | done |
| NFR-DEP-07 | A completed write MUST be committed: visible to other connections, and surviving the writing connection closing. | done |

### 7.3 Performance and cost — `NFR-PRF`

| id | Requirement | Status |
|---|---|---|
| NFR-PRF-01 | Sync cost MUST scale with changed documents, not catalogued ones. | done |
| NFR-PRF-02 | A search MUST complete without waiting on any external system other than the catalog database. | done |
| NFR-PRF-03 | A full reconciliation of a steady-state catalog SHOULD make no model calls. | done |

## 8. Interfaces

### 8.1 MCP tools (read-only)

| Tool | Input | Output |
|---|---|---|
| `kb_search` | `queries[]`, `types[]?`, `tags[]?`, `limit?` | hits: entry, score, `stale`, fetch hint; plus the normalized queries actually searched |
| `kb_get` | `id` | one entry in full; triggers a lazy refresh when stale (FR-REC-10) |

### 8.2 CLI / make targets

`kb-init` and `kb-test` are make targets. The module's own CLI
(`python -m kb`) carries the rest: `search`, `get`, `override set|clear`,
`gaps`, `migrate`, `status` (phase 5), then `sync`, `reconcile`, `discover`
and `export` with their phases.

### 8.3 Source configuration

`config/kb-sources.yaml`, gitignored with a tracked `.example`, following the
same pattern as `config/librechat.yaml` and `searxng/settings.yml`:

```yaml
reviewed: false                  # FR-CFG-05
defaults:
  mechanisms:                    # FR-CFG-07
    incremental:   { enabled: true,  every: 15m }
    full_scrape:   { enabled: true,  at: "03:00" }
    webhook:       { enabled: false }
    lazy_refresh:  { enabled: true,  stale_after: 24h }
sources:
  - id: confluence-eng
    kind: confluence
    enabled: true
    spaces: [ENG, PRODUCT, BIZ]
    type_from_labels: { kb-glossary: term, kb-product: product, kb-team: team }
    exclude_labels: [archive, draft]

  - id: gitlab-platform
    kind: gitlab
    enabled: true
    base_url: ${GITLAB_BASE_URL}          # FR-CFG-01
    token_env: GITLAB_TOKEN               # FR-CFG-04
    mechanisms:
      webhook: { enabled: ${KB_GITLAB_WEBHOOK_ENABLED:-false} }
    projects:
      - path: media-service               # a directory inside a larger repo
        scopes:
          - { dir: docs/ADRs, type: spec, tags: [adr] }
      - path: platform/specs              # a whole repo
        scopes: [{ dir: ".", type: spec }]
      - path: payments/core
        ref: main
        scopes:
          - dir: docs
            patterns: ["**/*.md", "**/*.mdx"]
            exclude: ["**/CHANGELOG.md"]

  - id: jira-product
    kind: jira
    enabled: true
    jql: 'project in (PAY, CHK) AND issuetype = Epic'

  - id: service-catalog
    kind: http_api
    enabled: false
    url: https://internal.example/api/services
    mapping: { id: id, title: name, body: description, owner: team }
```

## 9. Freshness mechanisms

All four are specified; each is switchable (FR-CFG-07) so nothing expensive runs
on every tick.

| Mechanism | Freshness | Cost | Covers the others' failure | Default |
|---|---|---|---|---|
| Incremental change feed (FR-REC-08) | minutes | low | — | on, every 15 min |
| Lazy refresh on read (FR-REC-10) | on demand | negligible | events lost while offline | on, `stale_after: 24h` |
| Full reconciliation (FR-REC-09) | nightly | highest | deletions, moves, missed events | on, nightly |
| Webhooks (FR-REC-11) | seconds | negligible | polling latency | off; enable per source |

## 10. Constraints and assumptions

- C1. No embeddings model is available; cross-language matching is achieved by
  generating multilingual titles, summaries and keywords once per changed
  document.
- C2. The only model available is a self-hosted OpenAI-compatible endpoint,
  used during sync and never on the query path.
- C3. Document content is read live by the calling agent with the user's own
  credentials, so source systems enforce their own permissions.
- C4. The catalog is shared and unfiltered in this version; restricted material
  MUST NOT be catalogued (operational rule, enforced by source selection).
- C5. Source documents and model drafts are untrusted input: data to store,
  never instructions to act on.

## 11. Out of scope

Embeddings; per-team enforcement; chunk-level indexing of document bodies;
Confluence and Jira webhooks; write tools that let agents create entries; a
spec-driven-workflow toolset.

**A graph source is the expected next step** and nothing here blocks it: code
entities extracted into a graph database become entries of type `system` whose
`location` points into the graph — one more adapter under FR-SRC-06.
Relationship questions ("what calls this service?") would need a dedicated tool,
which a flat catalog cannot answer.

## 12. Traceability and verification

- **Automated coverage.** `kb/tests/test_spec_coverage.py` parses this document,
  collects every requirement id and its status, and scans the test suite for
  `Covers: <id>[, <id>…]` claims. It fails when a *done* requirement has no
  claim, or when a test claims an id that does not exist here. Coverage is
  therefore a build failure, not a review comment.
- **Boundary.** `kb/tests/test_boundaries.py` walks the source to enforce
  NFR-ARC-01/02.
- **End to end** (once §13 phase 5 lands): `make kb-init`,
  `make kb-sync SOURCE=…`, then a CLI search returning the expected entry with a
  working fetch hint.
- **Retrieval quality.** ~30 real questions run by hand, recording how many
  return the right entry in the top 3 — the number that decides whether
  embeddings are worth adding later.

## 13. Delivery phases

| Phase | Contents | Status |
|---|---|---|
| 1 | This specification; corrections to `AGENTS.md` | done |
| 2 | Pure rules: normalization, ids and hashing, ranking, override merging, validation | done |
| 3 | Use cases against in-memory fakes: search, reconcile | done |
| 4 | Storage: `kb-db`, migration, Postgres adapter | done |
| 5 | Front doors: CLI, then the MCP server — usable end to end | done |
| 6 | Sources: Confluence, Jira and GitLab, plus discovery. The HTTP API and local files are deferred (§6.8) | done |
| 7 | The four freshness mechanisms, the scheduler, the webhook receiver | next |
| 8 | Agent tools and instructions | |
| 9 | Operator documentation | |

## 14. Open questions

| id | Question | Blocking |
|---|---|---|
| Q1 | Which user-identity placeholders LibreChat v0.8.7 supports in MCP headers, needed for per-team filtering later. | No — `audience` is recorded regardless |
| Q2 | Whether an embeddings endpoint will exist; if one does, hybrid retrieval is added behind the existing store port. | No |

## 15. Change log

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-24 | First specification. Phases 1–3 implemented against it. |
| 1.5.0 | 2026-09-24 | Phase 6 closed with three sources (Confluence, Jira, GitLab). FR-SRC-04 (HTTP API) and FR-SRC-05 (local files) moved to *deferred*: specified, built when a real use case appears rather than on speculation. |
| 1.4.0 | 2026-09-24 | Phase 6, part two: the GitLab source (FR-SRC-03) -- scopes, glob matching, blob SHAs as versions, per-project commit checkpoints -- and GitLab discovery. The HTTP API and local files remain planned. |
| 1.3.0 | 2026-09-24 | Phase 6, part one: source configuration with `${VAR}` interpolation and the review gate (FR-CFG-*), the Confluence and Jira sources (FR-SRC-01/02), sync in full and incremental modes (FR-REC-08/09), and discovery. GitLab, the HTTP API and local files remain planned. |
| 1.2.0 | 2026-09-24 | Phase 5 (front doors): added FR-CLI-01..04, FR-MCP-01..04, and NFR-DEP-07 (writes must actually commit — a defect the single-connection tests could not see). FR-AGT-01 and NFR-DEP-02 now done. |
| 1.1.0 | 2026-09-24 | Phase 4 (storage). Added FR-SRCH-10 (field weighting), which the Postgres adapter made an explicit decision rather than an implicit one. FR-OVR-06, NFR-DEP-01, NFR-DEP-05, NFR-DEP-06 now done. |
