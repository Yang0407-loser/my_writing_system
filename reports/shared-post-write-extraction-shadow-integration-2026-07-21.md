# Shared Typed Post-Write Extraction shadow integration

Date: 2026-07-21

## Outcome

The production-grade typed contract and default-off shadow hook were followed by the one authorized real shadow task. Isolation and evidence traceability worked, but the extractor failed the semantic-coverage and end-to-end latency gates. Its status is `real_shadow_not_promoted`; legacy extractors remain authoritative and the feature stays off by default.

## Real shadow result

The completed four-subsection task produced four shadow calls, 15,141 actual tokens and 56.629 seconds of extractor latency. That is 64.57% of the Writer token total, 29.98% of all task tokens and 15.75% of total task elapsed time. The extractor accepted 55 changes, rejected six proposed changes whose evidence was not found, and retained 100% character-span traceability for accepted changes.

The output was not consumer-ready:

- `handover` changes: 0. The Bundle did not explicitly represent legacy open threads.
- `character_state` changes: 28, but they used free-form names and predicates instead of stable character IDs and the arc state fields consumed by `CharacterManager`.
- Relationship, experience and event changes had exact evidence, but did not yet match the stable IDs, field semantics, aggregation rules or write timing required by their authoritative stores.
- The 55 changes included transient actions and location/presence observations. Evidence correctness alone therefore did not establish that each item was worth persisting as future writing state.

For the same task, the potentially replaceable legacy chain used seven calls and 21,703 known tokens: four handover calls used 10,592, one character-state call used 6,749, one relation call used 302, and one experience call used 4,060. Full replacement would theoretically save 6,562 tokens, or 30.24% of that extraction chain. This is not a realized saving: full field coverage was not proven, and the new extractor took 35.3 seconds longer than handover extraction alone before considering consumer timing and the existing background experience call.

The legacy character-state call produced no effective state update in this task, the relation call wrote no relation record, and experience extraction wrote nine event records. These are useful cost signals, not sufficient evidence to delete those paths globally from one sample.

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

## Decision

The one-task gate did not pass. No consumer may be migrated, no second same-shape Demo should be run, and Prompt or keyword expansion is explicitly out of scope. `WRITER_POST_WRITE_EXTRACTION_MODE` remains `off`; Writer output, checkpoint behavior and all authoritative stores remain unchanged.

If this architecture is revisited, the next work must be an offline contract and consumer-adapter redesign: define stable IDs, durable-vs-transient state rules, explicit open-thread semantics and per-consumer projections before making another model call. A new live experiment is not justified until that offline contract demonstrates that the Bundle can actually drive every retained consumer.

Engineering verification records 43 integration tests plus four real-shadow closure checks passed, and affected-module compileall passed. Implementation-time Writer/LLM calls were zero.
