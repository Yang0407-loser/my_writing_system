# Phase 4 Batch 1: Context Broker whole-item shadow experiment

Date: 2026-07-19
Decision: **the budgeted profile passes item-retention and token gates, but remains shadow pending generation-quality evaluation**

## Outcome

An independent `ContextBroker` now classifies and selects complete context items without modifying their text. It is not imported by Writer, does not alter `ContextManager`, and makes no LLM calls. Writer's legacy message hash remained unchanged for all 10 frozen scenarios.

The `budgeted_broker` profile reduced mean estimated input from **12,406.4 to 8,392.4 tokens**, a **32.35%** reduction. It retained 100% of hard-required items, immediately previous subsection originals, handover notes, late-query required items, all legacy top-5 RAG items, and all **4/4** human-supported evidence sources already present in legacy input. The other seven supported sources remain explicitly classified as the pre-existing retrieval ceiling.

This is not a production-promotion result. No generated prose was compared, and none of the 19 older recent-original items survived the 8,500-token budget. The next authorized experiment must therefore test whether omitting those complete older subsections harms continuity or prose quality.

## Profile comparison

| Profile | Mean tokens | Reduction | Older-recent retention | Required/evidence retention | Over-budget scenarios | Decision |
|---|---:|---:|---:|---:|---:|---|
| `legacy_full` | 12,406.4 | 0% | 100% | 100%; evidence 4/4 | 0 | Baseline |
| `continuity_first` | 12,406.4 | 0% | 100% | 100%; evidence 4/4 | 0 | Conservative selector found no safely droppable older item |
| `budgeted_broker` | 8,392.4 | 32.35% | 0% | 100%; evidence 4/4 | 2 | Passes Batch 1 gates; quality shadow only |

`continuity_first` intentionally treats ambiguous lexical evidence as a reason to retain the entire older subsection. It therefore produces no saving on this set. The budgeted profile preserves P0/P1/P2 first, then admits whole optional items only when they fit. Two scenarios exceed the 8,500-token soft target because protected items alone are larger than the budget; nothing is hard-truncated.

## Contracts and safety

- P0 protects the system/template skeleton, current writing goal, key points/mandatory events, hard character/relationship constraints, and rules with stored priority at least 8.
- P1 protects the immediately previous subsection and the handover note.
- P2 protects every current legacy top-5 RAG item; retrieval and reranking are unchanged.
- P3 contains the other two recent originals, non-locked rules, style/examples, world/event auxiliaries, and other soft guidance.
- Every item records IDs, type, requirement, priority, hash, character/token cost, injection position, actors, keep/drop reason, budget state, and provenance. Report traces never copy item text.
- `must_recall_facts`, gold sections, human relevance, supported-fact labels, and review conclusions are unavailable to the runtime Broker. Human evidence is loaded only after all selections finish.

The 10 real legacy retrievals ran read-only against the shared collection with the original `task_id` filter. They were executed in isolated local processes to bound cumulative Chroma/embedding resources; this does not change the query, index, filter, or returned top-5 behavior.

## Acceptance

The budgeted profile passes every Batch 1 mechanical gate:

- hard-required retention: 100%;
- immediately previous original: 100%;
- handover: 100%;
- legacy-present human evidence: 4/4;
- late-query required retention: 100%;
- source/hash/position traceability: 100%;
- Writer legacy message hash unchanged: 10/10;
- mean token reduction: 32.35%, above both the 20% batch gate and the 30% final token target.

Passing the token target does not prove writing quality. Production remains on the unchanged legacy Writer input. Phase 4 Batch 2 and production integration were not started.

## Recommended next authorization

Run a generation-quality shadow A/B on a small frozen set: legacy messages versus the `budgeted_broker` shadow messages, using identical model settings and deterministic evaluation. Focus human review only on continuity breaks, missing causal setup, character/relationship violations, and style degradation caused by dropping older whole subsections. Do not change Broker policy, RAG, Prompt, or Writer at the same time.

The machine-readable selection ledger and acceptance results are in `reports/phase4-batch1-context-broker-shadow.json`.

## Verification

- unit: 175 passed;
- integration: 8 passed;
- quality: 51 passed;
- `python -m compileall -q app tests`: passed.

The only test warning is Chroma's existing Python 3.16 deprecation warning. It is unrelated to this change.
