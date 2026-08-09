# Foundation Data Ownership v0

## Purpose and boundary

This document freezes the pre-Foundation ownership reality and the intended P2
authority boundary. It is an inventory, not a claim that the legacy system
already has transactional canonical state. In Foundation v0, tenant identifiers
are mandatory scope keys and audit context; complete tenant isolation remains a
P3B deliverable.

The core rule for P2 is: PostgreSQL owns accepted document revisions, project
state versions, ledger records, idempotency reservations, and outbox manifests.
Redis, Chroma, Markdown, and in-process objects are projections or delivery
mechanisms. A projection may lag; it must never decide whether a canonical
commit happened.

## Current-to-target ownership matrix

| Data/artifact | Current readers | Current writer and write point | Current classification | P2 authority and rebuild rule |
| --- | --- | --- | --- | --- |
| Draft text (`draft`) | Coordinator status/result, Reviewer, export | `Writer.run()` accumulates `full_draft`; Coordinator mirrors it to Redis/checkpoint | Duplicated mutable runtime value | Canonical subsection revisions in PostgreSQL are authoritative; materialized full draft is ordered by document/subsection ordinal. |
| `section_texts` / `full_draft` | Writer resume, Coordinator consistency/review/export | Writer mutates local dict/string after subsection generation; Coordinator copies to state, Redis and checkpoint | Split-brain working state | Each accepted subsection creates an immutable revision and moves one explicit Subsection Revision Head; full text is rebuilt from current heads. |
| Handover | Writer prompt assembly, Coordinator resume, continuity/review logic | Writer extracts and currently applies handover effects during the writing loop, then stores chain/history | Mixed candidate, mutation command and observation | Handover is candidate evidence only until atomic commit; accepted generic mutations/events enter canonical state/ledger in the same DB transaction. |
| `WorldStateManager` | Writer prompts, consistency checks, Coordinator result | Writer/legacy committer mutates Redis-backed facts during subsection execution | Premature mutable projection | Canonical project state version is authoritative; Redis world state is an idempotent outbox projection rebuilt from state/ledger. |
| `EventGraph` | Writer prompt ranking, review/analysis | Coordinator seeds arcs and Writer/legacy committer updates statuses in Redis | Premature mutable projection | Canonical ledger/state events are authoritative; EventGraph is rebuilt or incrementally projected after commit. |
| Chroma/vector chunks | Retrieval pipeline, Writer context selection | Writer/Coordinator call `VectorStore` after or during writing; deletion route cleans by task | External derived index | Outbox consumer writes idempotent revision-scoped chunks; rebuild from accepted revisions; never access inside the DB transaction. |
| `ContextManager` | Writer only | Constructed per `Writer.run`; composes transient prompt context and summaries | Ephemeral computation | Remains non-authoritative; inputs cite canonical revision/state IDs and retrieval projection version. |
| Redis task hash | HTTP status/stream endpoints, Coordinator, Writer helpers | Routes, Coordinator and Writer set progress, artifacts and observations throughout execution | Operational projection plus legacy state | Operational status/progress only; canonical content/state references carry revision/version IDs and can be rehydrated from PostgreSQL. |
| Redis stream | Polling client | Writer callbacks and Coordinator append events | Ephemeral delivery log | Delivery projection with bounded retention; deletion uses `Blackboard.stream_delete`; canonical outbox/ledger can recreate durable events. |
| Redis checkpoint | Coordinator resume, Writer resume helpers | Coordinator and Writer save phase-local state | Recovery cache; previously credential-bearing | Secret-free execution checkpoint. Canonical heads are references, not inferred from checkpoint order; cache loss must not lose accepted work. |
| `TaskStore` SQLite | History APIs | Coordinator saves completion metadata; history/delete routes mutate rows | Legacy metadata archive | Compatibility projection only after P2. Canonical DB owns projects/documents/revisions/state; projection rebuild uses committed records/outbox. |
| Markdown output | User download/manual inspection | Coordinator exports assembled draft near completion | Human-facing export | Derived export with source revision IDs and content hash; regenerable from canonical current heads. |
| StateFrame history | Writer prompt/audit helpers, checkpoint mirror | `StateFrameHistoryRecorder` captures before/after records around subsection work | Diagnostic runtime artifact | Observation/audit projection linked to candidate and committed revision; never advances either Head. |
| Style/anti-AI/commercial/integrity/world-pressure observations | Review and experiment tooling | Writer appends observations and mirrors selected collections into Redis/checkpoint | Non-authoritative observations | Store or project as observations linked to canonical IDs; a failure to persist them cannot partially commit content/state. |

## Lifecycle and transaction rules

1. Generation produces a Candidate with draft hash, base revision ID, base project
   state version ID, validation result, provenance and evidence.
2. A pure compiler produces the complete next state snapshot and ledger events.
3. One SQL transaction verifies both Heads and hashes, reserves idempotency,
   inserts immutable revision/state/ledger rows, moves both Heads, and inserts
   outbox manifests.
4. No transaction calls Redis, Chroma, an LLM, or the filesystem.
5. Projection workers claim outbox rows, write idempotently, and record success or
   retry metadata. Barrier state is observable and explicit.
6. Deleting or archiving a user-facing task removes operational projections but
   does not silently erase canonical revision or ledger history. Destructive
   canonical retention policy is outside Foundation v0.

## Scope and query rules

Every canonical repository operation accepts tenant and project scope. Document,
subsection, revision, project-state, ledger, idempotency and outbox lookups include
those predicates even when IDs are globally unique. Reads never infer the current
revision or state from `created_at`; they follow the two explicit Head pointers.
