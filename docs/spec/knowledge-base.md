# Knowledge base (`kb`) — software requirements specification

| | |
|---|---|
| **Spec ID** | SPEC-KB-001 |
| **Status** | Active — implemented (see §12; two source kinds deferred by choice) |
| **Version** | 2.8.0 |
| **Last updated** | 2026-10-14 |
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

- N1. Not a document store: entries never hold document bodies. The one
  exception is opt-in: a source an operator explicitly marks
  `store_raw_content` has its documents' text kept beside their entries, so
  agents can read it (§6.10, C6).
- N2. Not an authoring tool: knowledge stays where its authors already write it.
  The one deliberate exception is the `form` source (FR-SRC-07): short records an
  operator writes in the admin console for knowledge that has no home anywhere
  else.
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
| **Stored content** | One document's text, kept beside its entry only for a source marked `store_raw_content`, at the version the entry describes. Derived, like the rest of the catalog. |
| **Gap** | A search that matched nothing — evidence of knowledge nobody has written down. |

## 4. Users and acceptance scenarios

**Primary users:** everyone using the chat front door (product, business and
engineering), through an agent. **Secondary user:** the operator who configures
sources and reviews overrides.

| id | Scenario | Status |
|---|---|---|
| AS-01 | **Given** a wiki page titled "Payment reconciliation" describing refund retries, **when** a user asks "how do we handle refund retries?", **then** the agent finds that page and answers from its live content, naming it. | done |
| AS-02 | **Given** a document summarized in English and Persian, **when** a user asks in Persian, **then** the catalog returns it and the agent answers in Persian. | done |
| AS-03 | **Given** nothing in the catalog matches, **when** a user asks, **then** the agent says so and asks, rather than guessing, and the query is recorded as a gap. | done |
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
| FR-ENT-01 | An entry MUST record what a document is and where it lives, and MUST NOT store the document's body. Text kept for an opted-in source lives in the separate content store (§6.10), never in the entry. | done |
| FR-ENT-02 | An entry whose document could never be fetched MUST be rejected when it is built: a known source kind missing its reference key, or an unknown kind with no URL. The catalog's own kind, `form`, needs its `record` reference instead of a URL: it is read through `kb_get` (FR-CNT-12). | done |
| FR-ENT-03 | `title` and `summary` MUST be language-keyed maps, and an entry MUST have non-empty title text in at least one language. | done |
| FR-ENT-04 | Every entry MUST name the source that produced it. | done |
| FR-ENT-05 | Entry ids MUST be deterministic: the same document yields the same id from every mechanism and every run, and two sources MUST NOT collide. | done |
| FR-ENT-06 | Entries MUST be immutable, MUST reject unknown fields, and validation MUST report every problem in one error. | done |
| FR-ENT-07 | Entries MUST round-trip through JSON unchanged. | done |
| FR-ENT-08 | Catalog timestamps MUST be kept separately from the source's own timestamps. | done |
| FR-ENT-09 | An entry MUST expose a fetch hint, or none when no tool in this stack can read that source. | done |
| FR-ENT-10 | Every entry MUST carry an `audience` (default `all`); this version MUST NOT enforce it. | done |
| FR-ENT-11 | An entry MUST record the id its source knows the document by, so one entry can be re-read on its own. Entry ids are slugs, and slugs do not invert. | done |

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
| FR-REC-10 | Reading a stale entry MUST queue a refresh of that entry. | done |
| FR-REC-11 | A webhook MUST refresh only the paths in its payload, and MUST be rejected without a valid secret token. | done |
| FR-REC-12 | An operator MUST be able to force re-summarization of every document a sync sees, bypassing the content-hash gate, to recover entries a since-fixed summarizer defect left undescribed. | done |
| FR-REC-13 | A change to a document's source-derived classification -- its type or tags, such as a wiki page's labels -- MUST reach its entry on the next sync even when the content hash is unchanged, without a model call. | done |

### 6.4a Scheduling — `FR-SCH`

| id | Requirement | Status |
|---|---|---|
| FR-SCH-01 | The scheduler MUST run each mechanism at the cadence its configuration gives (`every: 15m`, `at: "03:00"`), per source, and MUST NOT run a disabled one at all. | done |
| FR-SCH-02 | Deciding what is due MUST be a pure function of the configuration, the last run times and the current time — testable without sleeping, and without a database. | done |
| FR-SCH-03 | A source whose run fails MUST NOT stop the other sources or the scheduler; the failure MUST be recorded and the next cadence attempted. | done |
| FR-SCH-04 | Every run MUST be recorded with its counts, duration and error, so "is this thing still working" is answerable after the fact. | done |
| FR-SCH-05 | A run MUST NOT start while another run for the same source is still going, however slow that one is. | done |
| FR-SCH-06 | A scheduled time of day MUST be interpreted in the deployment's own timezone (`KB_TIMEZONE`, an IANA name, default UTC), and an unknown zone MUST fail at startup naming the value. Every timestamp MUST be timezone-aware, so everything that compares instants stays correct regardless. | done |
| FR-SCH-07 | When a summarizer is configured, a scheduled run that would summarize (an incremental or full sync, or a lazy refresh) MUST first confirm the summarizer passes the same check `sync` requires (FR-CFG-09), and MUST NOT run, recording the failure as that run's error, when it does not. The verdict MAY be reused for a short while so several jobs due together cost one probe. | done |
| FR-SCH-08 | A run MUST be recorded when it starts, with status `running`, and updated when it ends with its final status (`success`, `warning` when documents were rejected, or `error`). A run still `running` when the scheduler starts MUST be closed as an error saying it was interrupted. | done |
| FR-SCH-09 | A run record MUST say what started it (`scheduled`, `manual`, `webhook` or `cli`), its mode, whether it was a dry run or forced, which documents were rejected and why, how many texts were stored and purged, and the checkpoint it ended on. | done |
| FR-SCH-10 | At startup the scheduler MUST seed each cadence's last-run time from the recorded runs, so a restart does not make every source due at once, nor make a nightly job wait another day. | done |
| FR-SCH-11 | A sync, a purge of stored text, or the retirement of a source's entries, requested from outside the scheduler MUST be queued and executed by the scheduler, one job at a time and never alongside another run of the same source (FR-SCH-05). A request MUST be cancellable until it starts, and MUST record the run it produced. | done |
| FR-SCH-12 | The scheduler MUST publish a heartbeat on every tick: when it last ran, whether syncing is approved and where that came from, the summarizer's verdict, and for each credential variable the source configuration names, whether it is set -- names and yes/no only, never a value. | done |
| FR-SCH-13 | When each source's mechanisms are next due MUST be computable as a pure function of the same inputs as FR-SCH-02, and MUST agree with it: a job `next_due` reports for "now" is a job `due` returns. | done |

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
| FR-CFG-05 | Syncing MUST be switchable off for every source at once, from the configuration (`approved: false`) or from the environment (`KB_SOURCES_APPROVED`), with the environment winning and every command reporting which one it used. It defaults to on: the gate that protects a newly discovered source is that source's own `enabled: false`. | done |
| FR-CFG-06 | Discovery MUST draft the config from what each system contains, writing every candidate `enabled: false`, and MUST NOT rewrite a line an operator has edited. | done |
| FR-CFG-08 | The summarizer URL MUST be an OpenAI-compatible **base** URL, normalized on the way in (a trailing `/chat/completions` or slash is accepted and stripped), and rejected at startup if it is not a URL at all. Compatibility MUST be verifiable against the running endpoint, not assumed. | done |
| FR-CFG-07 | Each freshness mechanism MUST be switchable globally and per source, and a disabled mechanism MUST do nothing. | done |
| FR-CFG-09 | Compatibility verification (FR-CFG-08) MUST include a real completions round-trip proving the endpoint honors `response_format` and the `system` role — reachability and a served model id are not sufficient, since an endpoint can answer both while silently ignoring the rest of the request. `sync` MUST refuse to run against a summarizer that fails this, rather than degrading every document to undescribed. | done |
| FR-CFG-10 | The admin console MUST change the source configuration only in two steps: a preview that validates the edited file with the same models the scheduler loads it with and returns a readable diff and warnings, then an apply that carries the preview's file fingerprint and an explicit confirmation. A changed fingerprint MUST refuse the apply. Nothing invalid MUST be savable. | done |
| FR-CFG-11 | An edit made from the console MUST preserve the file's comments and its `${VAR}` placeholders, MUST NOT write a resolved value, and MUST NOT accept a literal credential where a variable name is expected (FR-CFG-04). | done |
| FR-CFG-12 | Every change to the source configuration MUST be kept as a revision (what, when, before and after), and a change made to the file by hand MUST be detected and recorded the same way. The database copy of the sources MUST never override the file: the file is what the scheduler reads. | done |

### 6.7 Front doors — `FR-CLI`, `FR-MCP`

The CLI is the operator's door; the MCP server is the agent's. Both are thin:
they parse input, call a use case, and serialize the result.

| id | Requirement | Status |
|---|---|---|
| FR-CLI-01 | The CLI MUST print exactly one JSON document to stdout for every invocation, success or failure alike, and MUST exit 0 for any handled outcome — including a reported error. | done |
| FR-CLI-02 | The CLI MUST expose, at minimum: search, get, override (set and clear), gaps, migrate, and status. | done |
| FR-CLI-03 | A failure the operator can act on (no database, a malformed argument) MUST be reported as a structured error naming what failed, never as a traceback. | done |
| FR-CLI-05 | A `check` command MUST report whether each dependency is actually usable — the database, the summarizer endpoint, the source configuration — and name the one that is not, rather than leaving it to be discovered by a sync that quietly produces undescribed entries. | done |
| FR-CLI-06 | A `sync` run from the CLI MUST be recorded like a scheduled one (FR-SCH-08, FR-SCH-09), with trigger `cli`. | done |
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
| FR-SRC-06 | Adding a source MUST require only one adapter and one config entry. No source's name may appear in `domain/` or `application/`, with one deliberate exception: the fetch-hint map in `entry.py`, where adding a kind means adding a tool an agent can call — a decision, not a side effect. | done |
| FR-SRC-07 | The built-in source `form` MUST present the manual records written in the admin console (§6.11) as documents, so they reach the catalog through the same funnel as every other source (FR-REC-01). A record removed from it MUST be soft-deleted by the next full pass (FR-REC-09). Its text MUST always be kept (§6.10), and `form` MUST NOT be usable as the id of a configured source. | done |

### 6.9 Agent integration — `FR-AGT`

| id | Requirement | Status |
|---|---|---|
| FR-AGT-01 | `kb_search` and `kb_get` MUST be read-only, and therefore MUST NOT require a `toolApproval` entry. | done |
| FR-AGT-02 | The front-door agent MUST search the catalog before answering a question itself, MUST NOT answer from assumption, MUST answer in the user's language, and MUST name the source document it used. When it hands work to a specialist it MUST NOT search first: the specialist grounds its own work (FR-AGT-03), and a second search of the same question only adds latency. | done |
| FR-AGT-03 | Document-producing agents MUST ground their drafts in catalog results. | done |

### 6.10 Raw content — `FR-CNT`

Opt-in, per source. Designed in [`specs/001-kb-raw-content/`](../../specs/001-kb-raw-content/).
The sync already holds every body it summarizes, so keeping it needs no new
fetching and no credentials on the query path. What turning it on means is
stated in C6.

| id | Requirement | Status |
|---|---|---|
| FR-CNT-01 | Each source MUST accept `store_raw_content`, `false` when absent. Setting it under `defaults` MUST be a configuration error naming the setting: sharing is decided source by source. | done |
| FR-CNT-02 | For a source with `store_raw_content: true`, every sync that sees a document MUST leave its text stored at the source version the entry describes, whichever freshness mechanism ran. | done |
| FR-CNT-03 | For a source without the flag, no document text MUST be stored. | done |
| FR-CNT-04 | Storing or refreshing text MUST NOT, by itself, cause a model call: turning the flag on for an already-catalogued source fills its text without re-summarizing. | done |
| FR-CNT-05 | When a source's flag is off, any sync of it (other than a dry run) MUST delete all text stored for it. | done |
| FR-CNT-06 | An entry marked missing MUST lose its stored text, and stored text MUST be removed with its entry. | done |
| FR-CNT-07 | Text MUST be capped at `raw_content_max_bytes` (default 10 MB, at least 1 KB, settable in `defaults` and per source). Text over the cap MUST be stored truncated at a character boundary, marked truncated, with the document's original size. | done |
| FR-CNT-08 | `kb_get` MUST return stored text in pages (at most 24,000 characters) with the page's offset, the total length, the truncated flag, the original size and the next offset (none on the last page). It MUST accept an `offset`, and following `next_offset` MUST reproduce the stored text exactly. | done |
| FR-CNT-09 | Search MUST NOT match against or return stored text; results and their order MUST be the same with and without it. | done |
| FR-CNT-10 | The query path's role MUST be able to read stored text and MUST NOT be able to write it. | done |
| FR-CNT-11 | Stored text MUST be served only when it was read at the entry's current source version, and never for a soft-deleted entry. | done |
| FR-CNT-12 | When no tool in this stack can read a document live (FR-ENT-09 gives no fetch hint) but its text is stored, its search hit and `kb_get` result MUST name `kb_get` as the fetch hint. A document with a live, per-user tool keeps that tool. | done |
| FR-CNT-13 | `kb_get`'s description MUST tell an agent to answer from `content` when present, to say when it is truncated and point to the location for the rest, and otherwise to follow the fetch hint or location. | done |

### 6.11 Admin console — `FR-ADM`

A web console for the operator, in its own service (NFR-DEP-08). It is a human's
front door, not an agent's: nothing here is an MCP tool, and the one component
that holds source credentials, the scheduler, stays the only one that writes the
catalog -- the console asks it to, through a queue (FR-SCH-11). Designed in
[`specs/002-kb-admin-console/`](../../specs/002-kb-admin-console/).

| id | Requirement | Status |
|---|---|---|
| FR-ADM-01 | The console MUST refuse to start without an admin token of at least 32 characters, and MUST compare tokens in constant time. Without a valid session or the token, every endpoint but the health check MUST answer 401. | done |
| FR-ADM-02 | Login MUST issue a signed, `HttpOnly`, `SameSite=Strict` session cookie that stops working when the token changes. Repeated failed logins MUST be slowed down. | done |
| FR-ADM-03 | A request that changes anything MUST carry the console's own header and, when the browser sends an `Origin`, one that matches the console; otherwise it MUST be refused with 403. | done |
| FR-ADM-04 | The console MUST NOT write the catalog (entries, kept text, overrides, checkpoints). Its database role MUST be able to read everything and write only the console's own tables. | done |
| FR-ADM-05 | The overview MUST report what `status` reports (FR-CLI-04) and, besides, whether the scheduler is alive (FR-SCH-12), whether syncing is approved and where that came from (FR-CFG-05), what is queued, and how the last day's runs went. | done |
| FR-ADM-06 | Runs MUST be listable by source, status and mode, newest first and paged, and each run MUST be readable in full (FR-SCH-09). | done |
| FR-ADM-07 | The schedule view MUST list, for every source and mechanism, its cadence, its last run and its next due time (FR-SCH-13), and MUST show a disabled mechanism as disabled rather than omit it. | done |
| FR-ADM-08 | An operator MUST be able to request a sync of one source or of every enabled source, as incremental or full, as a dry run, and forced (FR-REC-12), and to cancel a request that has not started. The request MUST return at once; its progress and result are read from the run it produces. | done |
| FR-ADM-09 | An operator MUST be able to request that a source's kept text be purged (FR-CNT-05). The request MUST warn that the next sync restores it while the source still keeps text. | done |
| FR-ADM-10 | The console MUST list what needs attention: failed and warning runs with the documents they rejected, failed refreshes, and the gap log (FR-SRCH-07): what people searched for and did not find. | done |
| FR-ADM-11 | An operator MUST be able to create, edit and delete sources through the two-step flow of FR-CFG-10, including a source's mechanisms, its kept-text setting and the global approval switch. Turning kept text on MUST warn that every catalog user can then read that text (C6). | done |
| FR-ADM-12 | An operator MUST be able to create, edit and delete manual records for the `form` source, each with a title and a summary per configured language, a body, a type, tags, keywords and an owner, and MUST be told the record's text is readable by every catalog user. Saving MUST queue the sync that publishes it. | done |
| FR-ADM-13 | An operator MUST be able to ask for a summary of a record's content on demand. The result MUST be a draft: nothing is stored until the operator saves it, and a stored draft MUST reach the entry without another model call. Saving only the summary MUST still update the entry. | done |
| FR-ADM-14 | An operator MUST be able to read a stored record the way an agent does: in pages with the same offsets and totals as `kb_get` (FR-CNT-08). | done |
| FR-ADM-16 | An operator MUST be able to retire the entries of a source that is no longer configured, so they stop appearing in search. Retiring is the soft delete of FR-REC-05 -- the entries are reported, not erased, and come back if the source returns -- and removes their stored text (FR-CNT-06). Deleting a source from the console MUST say that its entries otherwise stay searchable, and offer to retire them. | done |
| FR-ADM-15 | No response MUST contain the value of an environment variable, a token or a password; a credential is reported only as set or not set. | done |

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
| NFR-DEP-03 | Scheduled work MUST run in its own service, not inside the MCP server. | done |
| NFR-DEP-04 | The webhook receiver MUST be a separate component on a private interface. | done |
| NFR-DEP-05 | The query path MUST use a least-privilege database role. | done |
| NFR-DEP-06 | The index MUST be rebuildable from sources; only the gap log and checkpoints require backup. | done |
| NFR-DEP-07 | A completed write MUST be committed: visible to other connections, and surviving the writing connection closing. | done |
| NFR-DEP-08 | The admin console MUST be its own service, off unless its compose profile is enabled, published only on a loopback address by default, given no source credentials and no `env_file`, and connected as its own least-privilege role (FR-ADM-04). | done |

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
| `kb_get` | `id`, `offset?` | one entry in full; triggers a lazy refresh when stale (FR-REC-10); for a source storing raw content, one page of the document's text (FR-CNT-08) |

### 8.2 CLI / make targets

`kb-init` and `kb-test` are make targets. The module's own CLI
(`python -m kb`) carries the rest: `search`, `get`, `override set|clear`,
`gaps`, `migrate`, `status` (phase 5), then `sync`, `reconcile`, `discover`
and `export` with their phases.

### 8.3 Source configuration

`config/kb-sources.yaml`, gitignored with a tracked `.example`, following the
same pattern as `config/librechat.yaml` and `searxng/settings.yml`:

```yaml
approved: true                   # FR-CFG-05; KB_SOURCES_APPROVED overrides it
defaults:
  raw_content_max_bytes: 10485760  # FR-CNT-07; per document
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
    store_raw_content: true               # FR-CNT-01; C6: readable by every kb user
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
  credentials, so source systems enforce their own permissions -- except for
  sources that store raw content (C6).
- C4. The catalog is shared and unfiltered in this version; restricted material
  MUST NOT be catalogued (operational rule, enforced by source selection).
- C5. Source documents and model drafts are untrusted input: data to store,
  never instructions to act on.
- C6. A source marked `store_raw_content` makes its documents' full text
  readable through `kb_get` by every user of the catalog, whatever their
  access in the source system. Like C4, this is an operator's decision, made per
  source in tracked configuration; there is no per-user check. Turning the flag
  off and syncing removes the text (FR-CNT-05).

## 11. Out of scope

Embeddings; per-team enforcement; per-user or per-team access to stored
content (C6 is all or nothing per source); chunk-level indexing of document
bodies, including searching stored content (FR-CNT-09);
Confluence and Jira webhooks; write tools that let agents create entries (the
admin console, §6.11, is operated by a person and is not a tool an agent can
call); a spec-driven-workflow toolset.

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
| 7 | The four freshness mechanisms, the scheduler, the webhook receiver | done |
| 8 | Agent tools and instructions | done |
| 9 | Operator documentation (`docs/KNOWLEDGE_BASE.md`) and setup automation | done |
| 10 | Opt-in raw content (§6.10), via [`specs/001-kb-raw-content/`](../../specs/001-kb-raw-content/) | done |

## 14. Open questions

| id | Question | Blocking |
|---|---|---|
| Q1 | Which user-identity placeholders LibreChat v0.8.7 supports in MCP headers, needed for per-team filtering later. | No — `audience` is recorded regardless |
| Q2 | Whether an embeddings endpoint will exist; if one does, hybrid retrieval is added behind the existing store port. | No |

## 15. Change log

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-24 | First specification. Phases 1–3 implemented against it. |
| 2.8.0 | 2026-10-14 | Added §6.11 (FR-ADM-01..16), FR-SCH-08..13, FR-CFG-10..12, FR-CLI-06, FR-SRC-07 and NFR-DEP-08: an admin console for the operator, in its own opt-in service. It reads run history and the catalog, queues syncs for the scheduler to run, edits the source configuration after an explicit approval, and holds manual `form` records with on-demand summaries. FR-ENT-02 now admits the catalog's own `form` kind; N2 and §11 say what that does and does not change. Run history gained a start-of-run record, a status and the trigger, because "is it running now, and who asked" was not answerable from the end-of-run row alone. |
| 2.7.0 | 2026-10-11 | Added FR-REC-13: a label-only change re-classifies the entry without re-summarizing. Adding or swapping a wiki label changes neither the page's text nor its version, so the content-hash gate treated it as unchanged and the entry kept its old type and tags until the text next changed. |
| 2.6.0 | 2026-10-11 | FR-AGT-02 narrowed: the front door searches the catalog before answering itself, not before delegating. Found in a live trial of the product-spec flow: the front door searched, then handed off to a specialist that searched the same question again, before doing everything itself. The front door is now a router with no write tools; the specialist it hands to grounds the work (FR-AGT-03). |
| 2.5.0 | 2026-10-06 | Added FR-SCH-07: the scheduler applies FR-CFG-09's summarizer check before any run that summarizes, as `sync` already did. Found live: with the summarizer's endpoint answering 403 to completions, the scheduler catalogued 39 new documents undescribed (a warning per document, nothing refused), and they had to be recovered with `sync --force`. |
| 2.4.0 | 2026-10-04 | Added §6.10 (FR-CNT-01..13) and C6: a source may opt into keeping its documents' text (`store_raw_content`), readable page by page through `kb_get`. N1, FR-ENT-01 and C3 narrowed accordingly: entries still never hold a body; the text sits in its own store, tied to the entry's version, purged when the flag is turned off. Prompted by GitLab ADRs: no tool in this stack reads GitLab live, so agents could only ever see their summaries. Designed with Spec Kit in `specs/001-kb-raw-content/`. |
| 2.3.0 | 2026-09-28 | Added FR-CFG-09: `check()` now proves structured output actually works with a real completions round-trip (a fixed probe: `system` + `response_format: json_object`, verified against the exact reply expected), not just that `/models` answers and lists the right id. `sync` refuses to run at all against a summarizer that fails it. Prompted by the same experimental backend as 2.2.0 below -- it turned out to not merely mis-format JSON sometimes, but to silently ignore the `system` role entirely, every time, while still answering 200 with a normal chat reply. An earlier fix folded the instruction into the `user` message to route around that one backend; reverted -- the request this module sends stays the plain OpenAI standard for every endpoint, and an endpoint that cannot honor it is something to report and refuse, not something to accommodate. |
| 2.2.0 | 2026-09-28 | Added FR-REC-12: `kb sync --force` bypasses the content-hash gate to recover entries a since-fixed summarizer defect left undescribed -- an unchanged run stops being free only when explicitly asked to. Prompted by an experimental OpenAI-compatible backend that didn't enforce the `response_format` it was sent, degrading ~38% of one sync; the summarizer itself gained a same-request retry with a sharper instruction (no spec change -- an internal robustness detail of FR-REC-06, not new externally-visible behavior), but that only helps content summarized *after* the fix, hence this flag for what came before it. |
| 2.1.0 | 2026-09-24 | Added FR-CFG-08 and FR-CLI-05: the summarizer URL is normalized and its compatibility is checkable against the live endpoint, and `kb check` reports which dependency is not ready. |
| 2.0.0 | 2026-09-24 | All nine phases delivered. `make up` now brings the catalog up with the stack and reports what is still needed; `docs/KNOWLEDGE_BASE.md` is the operator guide. Two source kinds remain deferred by choice. |
| 1.10.0 | 2026-09-24 | Added FR-SCH-06: times of day are read in `KB_TIMEZONE`. A nightly job written as `at: "03:00"` by people in Tehran was firing at 06:30 their time, because the clock reported UTC. |
| 1.9.0 | 2026-09-24 | `reviewed` became `approved`, defaulting to on and overridable by `KB_SOURCES_APPROVED`. The rename says what the flag is (a decision about syncing) rather than what somebody did (read the file), and the safety it was carrying was always really per source: discovery writes candidates `enabled: false`, and that is what keeps a newly found space out of the catalog. What is left is a coarse switch for an incident, which is more useful in the environment than in a file. |
| 1.8.0 | 2026-09-24 | Phase 8: the catalog tools are attached to the front-door agent and to the four document-producing agents, with the instruction that matters most -- search before assuming, answer in the user's language, read the real document, name it. |
| 1.7.0 | 2026-09-24 | Phase 7: all four freshness mechanisms are live. Lazy refresh is a queue (FR-REC-10) written by the read path and drained by the scheduler, so the MCP server keeps its read-only role and no source credentials; the GitLab webhook (FR-REC-11) enqueues and returns for the same reason. Added `entry.external_id`, without which one entry could not be re-read on its own. |
| 1.6.0 | 2026-09-24 | Phase 7: added FR-SCH-01..05 (scheduling). |
| 1.5.0 | 2026-09-24 | Phase 6 closed with three sources (Confluence, Jira, GitLab). FR-SRC-04 (HTTP API) and FR-SRC-05 (local files) moved to *deferred*: specified, built when a real use case appears rather than on speculation. |
| 1.4.0 | 2026-09-24 | Phase 6, part two: the GitLab source (FR-SRC-03) -- scopes, glob matching, blob SHAs as versions, per-project commit checkpoints -- and GitLab discovery. The HTTP API and local files remain planned. |
| 1.3.0 | 2026-09-24 | Phase 6, part one: source configuration with `${VAR}` interpolation and the review gate (FR-CFG-*), the Confluence and Jira sources (FR-SRC-01/02), sync in full and incremental modes (FR-REC-08/09), and discovery. GitLab, the HTTP API and local files remain planned. |
| 1.2.0 | 2026-09-24 | Phase 5 (front doors): added FR-CLI-01..04, FR-MCP-01..04, and NFR-DEP-07 (writes must actually commit — a defect the single-connection tests could not see). FR-AGT-01 and NFR-DEP-02 now done. |
| 1.1.0 | 2026-09-24 | Phase 4 (storage). Added FR-SRCH-10 (field weighting), which the Postgres adapter made an explicit decision rather than an implicit one. FR-OVR-06, NFR-DEP-01, NFR-DEP-05, NFR-DEP-06 now done. |
