# Phase 3 closure and Phase 4 entry context census

Date: 2026-07-19
Decision: **close Phase 3 experiments without promotion; Phase 4 may begin in shadow mode**

## Outcome

The main Writer-context problem is multi-source accumulation, not demonstrable copy duplication. Across the ten frozen writing requests, the reconstructed current Writer input averages **12,406.4 estimated tokens** and ranges from 10,511 to 14,480. The most recent three subsection originals are the largest source at 5,127.1 tokens (41.33%); legacy RAG is second at 3,068.0 (24.73%); fixed system/user prompt scaffolding is third at 1,104.2.

This uses the same local estimator as Writer, not the serving model tokenizer. It is a traceable reconstruction rather than a byte-identical replay: the historical task row and Redis state are unavailable. Exact golden-story subsection text and handover entries are combined with real, current legacy `task_id`-filtered Chroma results and the current Writer templates. Historical character cards, arcs, style profile, LLM-generated style behavior and WorldState/EventGraph values are not invented; the report marks them as audit overlays or unavailable.

## Actual assembly chain

1. `coordinator._run_writing_stage` loads task, outline and auxiliary contexts.
2. `Writer.run` obtains legacy RAG, formats characters/arcs, creates the handover brief, reads `ContextManager.get_summary()`, and adds world/event, rule and style fields.
3. `WRITING_PROMPT.format` and `WRITER_SYSTEM_PROMPT` form the two input messages.
4. Writer would call `_generate_with_retry`; this census stops before that boundary. It makes zero Writer or LLM generation calls.

`ContextManager` remains unchanged: it stores the most recent three subsection originals, ignores historical `running_summary` checkpoint fields, and exposes the buffer to Writer. Phase 4 may govern what is injected, but this audit does not change storage or Writer input.

## Mean token ledger

| Source | Mean estimated tokens | Share | Current requirement class |
|---|---:|---:|---|
| Recent 3 subsection originals | 5,127.1 | 41.33% | continuity-required, item-level governance allowed |
| Legacy RAG top-5 | 3,068.0 | 24.73% | evidence-required when human-supported; otherwise undecided |
| Fixed prompt | 1,104.2 | 8.90% | hard-required |
| Global/other rules | 797.0 | 6.42% | optional or rule-specific |
| Style instructions/examples | 641.0 | 5.17% | optional-context |
| Current writing goal/progress | 631.9 | 5.09% | hard-required |
| Character/relationship audit overlay | 589.2 | 4.75% | hard-required |
| Previous-section handover | 392.0 | 3.16% | continuity-required |
| World/event placeholders | 56.0 | 0.45% | unavailable in frozen replay |

The current code computes `style_structured` but neither Writer writing template references `{style_structured}`. It therefore consumes no Writer prompt tokens; the census records it as a computed but non-injected input rather than charging it to style.

## Noise and duplicate diagnosis

Deterministic exact, normalized containment and sentence-set Jaccard checks found **zero provably removable whole-block duplicates**. Low lexical overlap was not treated as irrelevance. This means a simple deduplicator cannot deliver the desired reduction on these samples: recent originals and RAG are both large even when they are not literal copies.

The conservative provable-duplicate saving is therefore zero. A separate non-required upper bound protects fixed/current/character/handover blocks, the immediately previous subsection, and human-supported RAG sources already present in the legacy input, then counts every other trimmable block. That mathematical ceiling averages 7,754.7 tokens (62.51%), but it is **not a removal recommendation**: older continuity and unlabeled RAG may still be useful.

## Required-content baseline

Every query has a traceable manifest using four result classes:

- `hard_required`: current writing goal/key points and relevant human-reviewed hard character/relationship constraints;
- `continuity_required`: the immediately previous subsection original and handover contract;
- `evidence_required`: human-confirmed `supports_which_fact` sources;
- `optional_context`: world/event, style and other auxiliary context pending Broker policy.

There are 11 human-supported query/source evidence items in the review set, but only 4 are present in the current legacy top-5 prompts. This is an existing retrieval ceiling, not a Broker loss. Phase 4 must retain 100% of those already present and report the seven absent items separately; it must not claim to repair recall merely by preserving input.

## Phase 3 closure

Phase 3 closes as **experiments not promoted**. Production remains the shared Chroma collection, original `task_id` filter and current legacy top-k. `QueryPlannerV2`, V2 reranking, sentence/structured compactors, EventChunker and event shadow retrieval remain experimental assets:

- sentence extraction lost fact evidence;
- structured windows did not meet evidence completeness and 20% reduction together;
- EventChunker passed the offline 9/9 and 21.43% feasibility test;
- direct event-vector retrieval failed precision, relevant-parent retention, late, fact-parent and token gates.

The 45 isolated events under task `80d1a9c6-4d8d-566a-82a7-192bd172d68c` remain for reproducibility. Cleanup was not executed; the previously tested exact-ID/profile guard remains the only allowed cleanup path.

## Phase 4 entry recommendation

Phase 4 may begin only as a shadow Context Broker. Its first priority is item-level governance of recent originals while preserving the ContextManager buffer, followed by total-budget accounting across RAG, fixed rules and style blocks. It should not begin with lossy sentence compression.

The first implementation gate is:

- retain 100% of hard-required items;
- retain the immediately previous subsection and required handover continuity;
- retain 100% of human-supported evidence sources already present in legacy input;
- record source ID, position, token cost and keep/drop reason for every item;
- compare new and legacy messages while Writer continues consuming the legacy messages.

Machine-readable ledgers, source contracts, Prompt hashes, real retrieval traces, duplicate pairs and per-query required manifests are in `reports/phase4-entry-context-census.json`.

## Verification

- Unit: 170 passed, 0 failed (one dependency deprecation warning).
- Integration: 8 passed, 0 failed (one dependency warning and three pre-existing `PytestReturnNotNoneWarning` warnings).
- Quality: 47 passed, 0 failed (one dependency deprecation warning).
- Python compileall: passed.
