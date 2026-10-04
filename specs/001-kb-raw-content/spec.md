# Feature Specification: Opt-in raw content for knowledge base sources

**Feature Branch**: `feat/kb-raw-content`

**Created**: 2026-10-04

**Status**: Draft

**Input**: User description: "Some documents (e.g. ADRs under `docs/` in a GitLab repository) are
in the knowledge catalog only as a summary. A user who cannot open the document in GitLab can ask
about the summary but cannot see or question the details. Sometimes the full content should be
readable through `kb`, sometimes not. No feature-rich permission system: a flag per source in the
sources configuration that stores the raw content in the catalog database. Name it
`store_raw_content`. Large documents are kept up to a generous limit; anything beyond it is
stored truncated and marked, so the agent has the summary and the truncated raw text."

## Clarifications

### Session 2026-10-04

- Q: Store text at sync time, or fetch it live from the source on each read? → A: Store it at sync
  time. The query service holds no source credentials and must not gain any. The sync already
  holds every body it summarizes.
- Q: What is the setting called? → A: `store_raw_content`. It names what the catalog does, which
  is the right level for whoever deploys `kb`. The documentation states the sharing consequence.
- Q: What happens to a document over the size limit: skip it, or store part of it? → A: Store it
  truncated at the limit and mark it, so the agent has the summary plus the truncated text.
- Q: How big is the limit? → A: Generous enough for medium and large documents (default 10 MB),
  configurable. Only unreasonably large files are cut.
- Q: Can the shared defaults turn the flag on for every source? → A: No. Sharing is decided
  source by source.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask about the details of a shared document (Priority: P1)

A user asks an agent a detailed question about an ADR, for example "which alternatives did the
storage ADR reject, and why?". The answer is in the document body, not in its summary. The ADR
lives in a repository the user has no access to, but the operator has marked that source as
shared in full. The agent finds the entry, reads the full text through the catalog and answers
from it, citing the document.

**Why this priority**: This is the gap that motivates the feature. Without it, nothing else here
delivers value.

**Independent Test**:

1. Mark one source as storing raw content and sync it.
2. Ask a question whose answer appears only in a document's body.
3. Confirm the agent answers it correctly, using no tool other than the catalog's.

**Acceptance Scenarios**:

1. **Given** a source marked to store raw content and synced at least once since being marked,
   **When** an agent reads one of its entries, **Then** the result includes the document's full
   text as it was at the version the entry describes.
2. **Given** an entry whose stored text is longer than one read returns, **When** the agent reads
   it, **Then** the result says how much text there is in total and where the next part starts,
   and reading from that point returns the next part with no gaps or overlaps.
3. **Given** a document that changed in the source, **When** the source is synced again, **Then**
   reading the entry returns the new text, never the old one.

---

### User Story 2 - Keep a source summary-only (Priority: P1)

Some sources must never have their content readable through the catalog, for example a
repository of security reviews. The operator leaves the flag off, or doesn't set it at all.
Agents still find those documents through their summaries, but the catalog holds and returns no
document text for them.

**Why this priority**: Storing text the operator didn't intend to share is the main risk of this
feature, so the safe behavior has to be the default and verifiable on its own.

**Independent Test**:

1. Sync a source without the flag.
2. Inspect the catalog's storage and read results.
3. Confirm no document text from that source is present in either.

**Acceptance Scenarios**:

1. **Given** a source with no `store_raw_content` setting, **When** it is synced, **Then** no
   document text from it is stored, and reading its entries returns no text.
2. **Given** a `store_raw_content: true` under the shared defaults rather than on a source,
   **When** the configuration is loaded, **Then** it is rejected with an error naming the
   setting, because sharing is decided source by source.

---

### User Story 3 - Stop sharing a source (Priority: P2)

The operator decides a source should no longer be readable in full. They turn the flag off. After
the next sync of that source, none of its document text remains in the catalog, and reading its
entries returns summaries only.

**Why this priority**: The sharing decision has to be reversible, and reversing it has to remove
the copies, not just hide them.

**Independent Test**:

1. With content stored for a source, turn the flag off and sync.
2. Confirm the stored text for that source is gone.

**Acceptance Scenarios**:

1. **Given** a source whose content was stored, **When** its flag is turned off and it is synced,
   **Then** all stored text for that source is deleted.
2. **Given** a document deleted from its source, **When** a full sync marks its entry missing,
   **Then** its stored text is deleted as well.

---

### User Story 4 - Very large documents (Priority: P3)

A source contains an occasional very large file, such as a generated reference or an exported
spreadsheet. The catalog stores medium and large documents whole. Anything beyond a configured
limit is stored truncated, marked as truncated, and records its original size. An agent reading
the entry knows the text is incomplete and where the full document lives.

**Why this priority**: This is rare, but without a limit one pathological file could bloat the
database, and without the marker the agent would present partial text as complete.

**Independent Test**:

1. Set a small limit for a test source.
2. Sync a document larger than the limit.
3. Confirm the stored text is cut at the limit and marked, and that the original size is
   reported.

**Acceptance Scenarios**:

1. **Given** a document larger than the configured limit, **When** it is synced, **Then** its
   first part up to the limit is stored and marked truncated with the original size, and the
   entry's summary is unaffected.
2. **Given** a document within the limit, **When** it is synced, **Then** it is stored whole and
   not marked truncated.

---

### Edge Cases

- **The flag is turned on for a source that was already synced.** Its existing, unchanged
  documents get their text on the next sync that sees them. This costs no extra model calls,
  because summaries are not regenerated just to store text.
- **A source returns an empty body for a document.** No text is stored, and the entry reads as
  having no content. It is not reported as an empty document.
- **The limit falls inside a multi-byte character** (Persian text). The truncation never splits
  a character; the stored text is always valid text.
- **A read starts at or beyond the end of the text.** It returns an empty part with no next
  position, not an error.
- **An entry is hidden by an override, or is soft-deleted.** A direct read still returns the
  entry and says it is hidden, but returns no text for a soft-deleted entry, because the source
  no longer has the document.
- **Text is stored while the entry is being re-summarized.** Text and summary always describe
  the same source version; text for an older version is never returned with a newer entry.
- **Search.** Stored text is never matched against search queries and never returned by search,
  so enabling the flag doesn't change search results or their ranking.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each source MUST accept a `store_raw_content` setting, `false` when absent.
- **FR-002**: `store_raw_content` MUST NOT be settable in the shared defaults. A configuration
  that sets it there MUST be rejected with an error naming the setting.
- **FR-003**: For a source with `store_raw_content: true`, every sync that sees a document MUST
  leave the catalog holding that document's text at the version the entry describes.
- **FR-004**: For a source without the flag, the catalog MUST NOT store any document text.
- **FR-005**: Storing or refreshing text MUST NOT, by itself, cause a document to be
  re-summarized.
- **FR-006**: When a source's flag is off, a sync of that source MUST delete any text previously
  stored for it.
- **FR-007**: When an entry is marked missing (deleted in its source), its stored text MUST be
  deleted. When an entry is removed from the catalog, its stored text MUST be removed with it.
- **FR-008**: A text-size limit MUST apply. It MUST be configurable and default to a value that
  holds medium and large documents whole (10 MB).
- **FR-009**: Text over the limit MUST be stored truncated at a character boundary, marked as
  truncated, and record the document's original size.
- **FR-010**: Reading an entry MUST return its stored text, if any, in parts of bounded size. Each
  read MUST report:
  - the part;
  - the total length;
  - whether the stored text is truncated;
  - the position of the next part, or that there is none.
- **FR-011**: Reading MUST accept a starting position. Consecutive reads following the reported
  next position MUST reproduce the stored text exactly.
- **FR-012**: Search MUST NOT match against or return stored text.
- **FR-013**: The catalog's read-only query service MUST be able to read stored text, and MUST
  NOT gain write access or source-system credentials to do so.
- **FR-014**: The read tool's description MUST tell agents:
  - when text is present, it may answer from the text;
  - when text is truncated, it says so and points to the document's location for the rest;
  - when no text is present, it follows the entry's fetch hint or location as before.
- **FR-015**: Operator documentation MUST state that turning the flag on makes the source's full
  text readable by every user of the knowledge base, whatever their permissions in the source
  system, and MUST explain how to revoke it.

### Key Entities

- **Source setting `store_raw_content`**: a per-source decision to keep document text. Absent
  means no.
- **Stored content**: one document's text, kept for one catalog entry. It records:
  - the source version it was read at;
  - whether it was truncated;
  - the original size;
  - when it was stored.

  It belongs to exactly one entry and never outlives it.
- **Content page**: what one read returns: a bounded slice of stored content with its starting
  position, the total length, the truncation marker and the next position.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a shared source, an agent answers questions whose answers appear only in a
  document's body. Sample questions are taken from five ADRs, and the agent reads only through
  the catalog.
- **SC-002**: After a sync of a source without the flag, the catalog holds zero bytes of that
  source's document text.
- **SC-003**: After turning a source's flag off and syncing it once, zero bytes of its text
  remain.
- **SC-004**: Turning the flag on for an already-synced source and running a full sync makes no
  model calls for unchanged documents.
- **SC-005**: Search results and their order for a fixed set of queries are identical with the
  flag on and off.
- **SC-006**: A document of 1,000 typical ADRs' combined size (about 20 MB of text) is stored and
  read back page by page without loss up to the limit, and marked truncated beyond it.

## Assumptions

- Sources yield document bodies as text today (GitLab Markdown, Confluence page bodies, Jira
  issue text), so storing what the sync already holds needs no new fetching.
- Sharing is all-or-nothing per source. Finer scoping, such as per-directory, per-team or
  per-user, is out of scope. The entry's existing `audience` field stays unenforced.
- A per-user GitLab read tool (users reading files with their own GitLab access) is a separate
  item on the README todo list.
- The query service's existing read-only database role is extended to read stored text. No new
  service or credential is introduced.
- Page size is a fixed implementation choice, sized to fit comfortably in a model's context
  window (about 24,000 characters), and not an operator setting.
