# Shared Typed Post-Write Extraction shadow integration

Date: 2026-07-21

## Outcome

The production-grade typed contract and a default-off shadow hook are implemented. No legacy extractor was removed, no authoritative state store gained a new write path, and no Writer/LLM call was made during implementation. The change is ready for one separately authorized real shadow task; it is not ready to replace production extraction.

## Consumer audit

The legacy path is not a set of interchangeable duplicate calls:

| Producer | Frequency and order | Production consumers | Replacement risk |
|---|---|---|---|
| `Writer._extract_handover` | Every subsection, synchronous, before length adjustment and commit | EventGraph arc status, WorldState facts, handover chain, backrefs and next-section context | High |
| `CharacterManager.update_states` | Section end when arcs exist | Arc `current_state`, Blackboard and later character context | High |
| Relation extraction | Section end, synchronous | `character_relations.db`, future prompt context and API | High |
| Experience extraction | Section end, background | sole `events.db` owner through `event_store`, future experience context and API | High |
| Handover foreshadowing | Part of handover extraction | Handover chain; it does not directly update `foreshadowings.db` | Medium |

This means direct deletion would change current-run state timing, checkpoint content and future prompts. Shadow must prove semantic coverage before any consumer is migrated.

## Contract

`PostWriteStateBundle` contains a committed output hash, sanitized source manifest, stable change IDs, typed categories, epistemic status, confidence and exact character spans. Categories cover handover, character state, relationship, time, location, presence, event, experience and foreshadowing. Status is restricted to `confirmed`, `unknown` and `conflicted`.

The extractor accepts a change only when `evidence_text` is an exact contiguous substring of the committed output. Hallucinated evidence, invalid categories/statuses and incomplete fields are rejected and counted as warnings. This is deliberately stricter than the legacy free-form strings.

To support legacy `resolved_events` and `arc_progress` coverage without guessing IDs, Writer freezes a read-only known-context before legacy handover effects run. It contains character IDs/names, current arc states and at most ten relevant EventGraph references. Only a hash-derived source ID appears in public telemetry; values remain inside the private task Bundle.

## Integration and safety

Configuration:

```cmd
set WRITER_POST_WRITE_EXTRACTION_MODE=off
```

- `off` is the default and constructs no extractor, makes no call and writes no record.
- `shadow` runs once after each successful subsection commit. It does not update WorldState, EventGraph, character arcs, relations, events, foreshadowings or checkpoints.
- Private Bundle values are stored only in the task-scoped Blackboard field `post_write_extraction_shadow` for later comparison.
- Public logs contain only hashes, category/count metrics, elapsed time and error type. They contain no draft, Prompt, messages or extracted values.
- Extractor tokens use the separate `post_write_extraction` cost label.
- Extractor and sink failures are converted to `shadow_error`; they cannot roll back committed text, trigger Writer retry or fail the task.

## What has not been proven

Shadow temporarily adds one LLM call per committed subsection. No production token or latency savings are claimed yet. A single real shadow task must measure whether one Bundle covers every legacy field still consumed by production, preserves confirmed facts, keeps unknowns unknown and provides 100% source/evidence traceability.

Only after that gate passes may a separate canary replace legacy consumers one category at a time. Failure ends this direction without expanding the Prompt, keyword lists or paper test matrix.

Engineering verification records 43 targeted tests passed and affected-module compileall passed. Implementation-time Writer/LLM calls were zero.
