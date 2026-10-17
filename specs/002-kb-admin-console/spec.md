# Feature Specification: Knowledge base admin console

**Feature Branch**: `ccr-9efb8e78-9y8x42`

**Created**: 2026-10-14

**Status**: Implemented (requirements are `done` in the spec of record)

**Input**: User description: "A UI for knowledge base management: see sync status and reports,
schedule and trigger syncs and see their results, create, edit and delete sources including ones
added from the UI beyond the declarative file, add manual records (a built-in `form` source),
manage them, and generate a summary on demand from a record's content. Admin only."

The requirements themselves live in [`docs/spec/knowledge-base.md`](../../docs/spec/knowledge-base.md)
(§6.11 `FR-ADM-*`, `FR-SCH-08..13`, `FR-CFG-10..12`, `FR-CLI-06`, `FR-SRC-07`, `NFR-DEP-08`). This
file records the decisions and the user stories behind them.

## Clarifications

### Session 2026-10-14

- Q: Who may use it, and how do they sign in? → A: One shared admin token from `.env`; no roles.
  A person with the token can do everything the console can.
- Q: Where does the front end live? → A: A React + Vite single-page app in `kb/frontend`, built
  into the `kb` image and served by the console service itself.
- Q: Should sources added from the UI live in the database instead of the YAML file? → A: No. The
  scheduler keeps reading the file, so the file stays the only truth. The console edits the file
  after an explicit, readable approval, and mirrors each change into the database for history.
- Q: Does the console start with the stack? → A: No. It is an opt-in compose profile (`admin`).
- Q: Can the console write the catalog? → A: Never. Its database role cannot. Anything that changes
  the catalog is a request the scheduler executes; the scheduler remains the one writer.
- Q: How does a manual record get a summary? → A: The operator asks the model for a draft, edits
  it, and saves it. What is saved is used as written; the model is not asked again at sync time.

## User Scenarios & Testing

### User Story 1 - See what the catalog is doing (Priority: P1)

An operator opens the console and sees whether the scheduler is alive, whether syncing is on, how
many runs succeeded in the last day, what is queued, and what needs attention.

**Independent Test**: With a heartbeat and some runs in the database, `GET /api/overview` reports
them, and a silent scheduler is reported as an error.

### User Story 2 - Trigger a sync and watch it finish (Priority: P1)

An operator picks a source and a mode (or a dry run, or a forced re-summarization), and the
request is queued. The scheduler runs it and the console shows queued, running, then the report.

**Independent Test**: Post a sync request, run one scheduler tick, and read back a run whose
trigger is `manual` and whose counts match.

### User Story 3 - Change what is synced (Priority: P2)

An operator adds, edits or deletes a source, changes its cadence, or switches syncing off. They
are shown the exact file diff and warnings (for example, that storing a source's text makes it
readable by every catalog user) and must confirm before anything is written.

**Independent Test**: Preview never writes; apply writes exactly the previewed diff and is
refused with a conflict if the file changed in between.

### User Story 4 - Write down what is written nowhere else (Priority: P2)

An operator writes a record in one or more languages, optionally asks the model to draft a summary,
edits and saves it. It reaches the catalog through the scheduler and is findable and readable by
agents through `kb_search` and `kb_get`.

**Independent Test**: Save a record, run the scheduler, search for it, read it in pages; edit only
its summary and see the entry change with no model call.

### Edge Cases

- The scheduler dies mid-run: the run is closed as interrupted at the next start.
- The sources file is being edited by hand: the console reads leniently, and a hand edit is
  recorded as a `file` revision; the file wins.
- A deleted source's entries stay searchable: the console offers to retire them.
- The model is down when a summary is requested: the operator is told, and can write one by hand.

## Requirements

See the spec of record. Feature ids map to it as follows.

| Spec of record | Meaning |
|---|---|
| FR-ADM-01..04 | token auth, session cookie, CSRF, no catalog writes |
| FR-ADM-05..07, 10 | overview, runs, derived schedules, issues |
| FR-ADM-08, 09, 16 | trigger a sync, purge kept text, retire a vanished source's entries |
| FR-ADM-11, FR-CFG-10..12 | source editing with approval, preserved comments, revisions |
| FR-ADM-12..14, FR-SRC-07 | manual records, on-demand drafts, reading as an agent |
| FR-ADM-15 | no response carries a credential |
| FR-SCH-08..13, FR-CLI-06 | run history at both ends, request queue, heartbeat, next due |
| NFR-DEP-08 | opt-in service, loopback port, `kb_admin` role, no source credentials |
