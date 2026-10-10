# Product flow: from an idea to developer-ready work

agentflow prepares work. Its job is to turn a product idea into a
**product spec** that developers can start from without asking anyone
anything, and into Jira tickets that point at that spec. Implementation
happens on the developers' side, in their own repositories and tools.
agentflow stops at the ticket.

The flow is spec-driven in the same sense as
[GitHub Spec Kit](https://github.com/github/spec-kit). Spec Kit splits a
feature into a **spec** (what and why, for any reader), then a **plan** and
**tasks** (how, for engineering). Product owns the spec. Engineering owns
everything after it. The product spec agentflow writes has the same shape as
Spec Kit's `spec.md`, so developers take it straight into Spec Kit's plan step.

**Specs and docs are the source of truth for how the product behaves.**
Product people don't read code and don't need GitLab access. Agents learn the
current behavior from the knowledge base (`kb`): the wiki, Jira, and the
repository docs the operator has chosen to sync. Where the docs are silent,
that becomes a question for engineering, never a guess.

## The stages

Start in **Product Assistant** with something like "spec this: …", "write the
requirements for …" or "prepare … for development". It routes the request
to **Prepare for Dev**, which drives the stages below and tells you which one
you're in. A spec can be paused and resumed in a later conversation ("continue
the spec for …").

| Stage | What happens | Who does it |
|---|---|---|
| 0. Understand | Current behavior is collected from the knowledge base and the documents behind it. Related Jira work is checked. A raw idea goes to **Plan** first, to be shaped. | Prepare for Dev, Plan |
| 1. Specify | A first draft of the product spec, published straight away as a wiki page labelled `spec-draft` | Doc Gen (`product-spec`), Docs manager |
| 2. Clarify | At most five questions per round, each with a recommended answer. Every answer is written back into the spec. Questions only engineering can answer are posted as a comment on the page, to a named person or a role ("Backend team lead"). Replies are read and folded in when you resume. | Prepare for Dev (`spec-clarify`), Docs manager |
| 3. Readiness | The spec is checked against Spec Kit's requirements checklist plus product checks. It is not ready while any open question remains. | Prepare for Dev (`spec-readiness`) |
| 4. Approve | You approve that exact version. The page is relabelled `spec-approved` and its Status line gets a revision number and date. | You, then Docs manager |
| 5. Tickets | One Epic per spec and one Story per user story, each with its acceptance criteria and a link back to the spec | Tasks |

Every write (wiki page, comment, label, ticket) still asks for your
confirmation first, as it always has.

## The product spec

The spec is generated from the `product-spec` skill in `agent-skills`. It has:

- **Current Behavior** and **Intended Behavior**: what happens today, citing
  the documents it came from, and what changes.
- **User Scenarios & Testing**: prioritized user stories (P1 first). Each has
  a "why this priority", an independent test, and
  `Given … When … Then …` acceptance scenarios.
- **Requirements**: `FR-001`, `FR-002`, … and the key entities in business
  terms.
- **Success Criteria**: `SC-001`, …, measurable and technology-free.
- **Assumptions**, **Out of Scope**, **Dependencies**, and **Questions for
  Engineering**, which must be empty before approval.
- A **Clarifications** log: `Q: … → A: …`, by session.

It never says how to build anything. Ids never change once published; a
revision adds new ids and marks removed ones.

The wiki page ends with a collapsed **"Spec Kit source (spec.md)"** block
holding the exact Markdown, kept in step with the page on every update.

## Labels and the knowledge base

| Label | Meaning | In the knowledge base |
|---|---|---|
| `spec-draft` | under discussion, not final | a `spec` entry tagged `spec-draft`; agents present it as a draft |
| `spec-approved` | the agreed version (see its Status line for the revision) | a `spec` entry tagged `spec-approved` |

For this to work, the operator maps both labels to `spec` in the wiki
source's `type_from_labels`, and makes sure the space where specs are written
is a source ([`KNOWLEDGE_BASE.md`](KNOWLEDGE_BASE.md)). A new or changed
page reaches the catalog on its next sync. Agents read the live page when
they need the latest text.

## What developers receive

- **An approved spec page**, labelled `spec-approved`, findable in the
  knowledge base.
- **An Epic**, if the project uses epics, linking to the spec and its
  revision.
- **One Story per user story**, labelled `spec-<feature>`, with this
  description:

  ```
  <the user story>

  *Spec story:* US-<n> (P<priority>)
  *Spec:* [<spec title>|<page URL>] (revision N)

  h3. Acceptance criteria
  # *Given* …, *When* …, *Then* …

  h3. Definition of Done
  * …
  ```

Sub-tasks stay with engineering: each specialty creates its own under the
Story, as the Tasks agent's rules already say.

To continue with Spec Kit:

1. Copy the page's "Spec Kit source (spec.md)" block into
   `specs/NNN-<feature>/spec.md` and fill in `**Feature Branch**`.
2. Go to `/speckit-plan`. `/speckit-clarify` should find nothing to ask; if it
   does, that is a gap in the spec, worth raising with product.
3. Continue with `/speckit-tasks` and `/speckit-implement`, or with the
   `task2code` skill, one Story at a time. Its acceptance criteria are already
   in the format it reads.

## When the spec changes after approval

Ask Prepare for Dev to "update the spec for …". The page goes back through
clarify, readiness and approval, and gets the next revision number. Tasks then
**resyncs** the tickets: it finds them by the `spec-<feature>` label and shows
a diff (new stories, changed acceptance criteria, removed stories). It writes
only what you approve. A removed story is closed or kept, never deleted.

## Where each piece lives

- **The skills** (`product-spec`, `spec-clarify`, `spec-readiness`) are in
  `agent-skills`, pinned by this repo's submodule. They are generic: no team,
  space or label names.
- **The team's conventions** are in the agent definitions in `agents/`:
  labels, the ticket shape, who drives which stage. Edit those files and apply
  them with `make agent-import` ([`AGENT_SYNC.md`](AGENT_SYNC.md)).
