# Context consistency refactor — Batch A execution report

Date: 2026-07-17  
Scope: Phase 0–2 only. Writer prompt composition, embedding model, retrieval ranking and collection strategy were not switched.

## Delivered

### Phase 0 — repeatable baseline

- Reused the existing 18-section golden story; no paid regeneration.
- Pinned the story by SHA-256 and fixed six objective style samples.
- Upgraded the 10-query RAG annotation to schema v2 with intent, gold sections/chunks, must-recall facts and causal-retrieval need.
- Added 30 provisional character constraints across 林晚、周野、季晴.
- Added a deterministic standard-library baseline runner and JSON/Markdown reports.
- Corrected objective dialogue measurement to recognize Chinese `“…”` quotes.

Baseline:

| Metric | Value |
|---|---:|
| Precision@5 | 68.0% |
| Recall@5 | 66.7% |
| Late-chapter Precision@5 | 40.0% |
| Objective style range violation | 0.0% |
| Human-confirmed character-label coverage | 0.0% |
| Historical per-subsection token coverage | 0.0% |

The last two values are explicit gaps, not inferred successes: Redis was unavailable and character annotations still require a person to confirm them.

### Phase 1 — ownership and deprecated-path audit

- Added `ContextItem`, canonical StoryChunk metadata, target NarrativeEvent and directed typed `EventEdge` contracts.
- Published state ownership, module inventory, overlap matrix and Writer-source mapping.
- Removed ExperienceTimeline’s live dual-write/dual-read behavior; `event_store` is now its sole persistence owner while the old API stays compatible.
- Kept `experience.db` untouched as a recoverable legacy backup.
- Marked uncalled `MemoryFuser` deprecated without deleting the user-modified file.
- Updated active documentation to describe the implemented four primary style controls instead of the historical 50-dimension design.
- Replaced remaining `except Exception: pass` paths with scoped fallback logging.

### Phase 2 — RAG observability and lifecycle

- `search_with_meta` now emits document ID, rank, distance, derived score, complete metadata, applied filter and elapsed time.
- `RAG_TRACE_CANDIDATE_K` can log a larger coarse candidate set while returning the legacy top-k to Writer; default `0` keeps old production behavior.
- Writer RAG logs now retain retrieval traces and deterministic usage classifications (`exact_or_near_exact`, `possible_paraphrase`, `not_observed`).
- New chunks reject empty text, skip exact same-task duplicates, serialize complex metadata deterministically and add content hash/source version/timestamp.
- Task deletion now attempts task-scoped vector cleanup; chunk limits are enforced after ingestion batches.
- New Writer runs record estimated input/output tokens, per-context-block token estimates, generation latency and rewrite count per subsection.

## Chroma benchmark decision

Chroma 1.5.9 smoke results:

| Tasks | Chunks/task | Filter mean | Per-task mean | Parity on successes | Per-task errors |
|---:|---:|---:|---:|---:|---:|
| 1 | 100 | 1.500 ms | 1.284 ms | 100% | 0 |
| 10 | 100 | 1.228 ms | 1.182 ms | 100% | 0 |
| 100 | 100 | 3.736 ms | 7.667 ms | 100% | 2/30 |
| 1 | 1,000 | 3.616 ms | 1.418 ms | 100% | 0 |
| 10 | 1,000 | 4.967 ms | 1.631 ms | 100% | 0 |

Decision: keep the shared collection plus `task_id` metadata filter. At 100 tasks, per-task collections were slower and produced two Rust HNSW reader failures. On 2026-07-18 the full 5,000-chunk matrix was explicitly waived for the Phase 3 preflight because it has high local time/storage cost and cannot alter the current no-migration decision. The waiver ends on a Chroma/HNSW upgrade, production scale approaching 5,000 chunks per task or more than 100 active tasks, filtered-query p95 above 50 ms or 2× baseline, an isolation/cleanup failure, or any proposal to migrate collection strategy.

## Verification

Commands:

```text
.venv\Scripts\python.exe -m pytest tests\unit -q
.venv\Scripts\python.exe -m pytest tests\integration -q
.venv\Scripts\python.exe -m pytest <20 new targeted tests> -q
.venv\Scripts\python.exe tests\benchmarks\benchmark_chroma_isolation.py --profile smoke --repeats 3
python -m tests.quality.baseline --output-dir reports
```

Results:

- New targeted tests: 20 passed.
- Unit baseline before: 99 passed, 11 failed.
- Unit after Phase 3 preflight reconciliation (2026-07-18): 127 passed, 0 failed, 1 dependency deprecation warning.
- Integration after Phase 3 preflight reconciliation: 7 passed, 0 failed, 3 existing `PytestReturnNotNoneWarning` warnings.
- Python compileall: passed.

The 11 outdated assertions were reconciled without restoring removed behavior: four ContextManager tests now assert the recent-three-subsection raw buffer contract, seven style tests/fixture now assert the four primary style controls, and a historical checkpoint test verifies that legacy `running_summary` fields are ignored while recent raw context is retained.

## Phase 3 preflight closure (2026-07-18)

- Added a manual review table for exactly 19 hard character rules at `tests/quality/character_consistency_hard_rules_review.md`. Human review is complete: 17 rules are `human_confirmed`; `linwan-10` and `jiqing-10` are `human_flagged_issue` and remain current-draft violations.
- `linwan-10` is now an explicit 林晚—周野 relationship-stage constraint with the gate `观察者→共同劳动者→可信赖伙伴→浪漫关系`; section 16 is included because it contains the actual romantic escalation.
- `jiqing-10` retains its realism boundary. The recorded defect is evidence-specific: the amount is stated, but post-year-end repayment, source of funds, post-resignation capacity and downside risk are not.
- Character stability constraints, relationship-stage constraints and current-draft quality defects are represented separately. A valid rule violated by this draft is not treated as an invalid rule.
- Kept ContextManager v0.9.2: recent three subsection originals plus the existing handover path; `running_summary` remains removed.
- Kept the four primary style controls: `emotion_intensity`, `dialogue_ratio`, `sentence_preference`, `sensory_density`. Historical compatibility fields remain secondary; the deleted 50-dimensional contract was not restored.
- Registered mechanical counting, repetitive sentence patterns and insufficient emotional layering as a separate qualitative style baseline issue. This round does not modify Writer and does not attribute the issue to removal of the legacy 50-dimensional fields without an A/B test.
- Recorded the bounded Chroma 5,000-chunk waiver and its mandatory re-test triggers. Shared collection + `task_id` metadata filter remains the production strategy.
- Annotation consistency tests are 8/8; unit is 127/127; integration is 7/7. Phase 3 implementation has not started.

## Risks and incomplete gates

- The working tree already contained extensive uncommitted user changes. The 2026-07-18 checkpoint audit must use an explicit allowlist and exclude local artifacts, generated output and secrets.
- All 19 hard labels are human-reviewed. Two violations are deliberate baseline defects, not missing labels: relationship-stage pacing (`linwan-10`) and incomplete financial-risk grounding (`jiqing-10`).
- Historical per-subsection metrics cannot be reconstructed from the exported story; new runs will collect them.
- Chroma smoke data is synthetic and does not measure embedding relevance.
- The full 1/10/100 task × 100/1,000/5,000 chunk matrix is not complete and is temporarily waived under the documented triggers above.

## Phase acceptance

- Phase 0: accepted for the annotation gate. Offline RAG/style baseline is repeatable; hard-rule human coverage is 100% with 17 satisfied and 2 violated. Historical token telemetry remains unavailable for the reused story and will only exist for new runs.
- Phase 1: accepted for audit/compatibility scope. Dedicated versioned CharacterState persistence remains a declared Phase 7 gap.
- Phase 2: accepted for observability and smoke-benchmark scope. Production strategy remains unchanged; the full-scale matrix is explicitly waived until a documented trigger occurs.
- Phase 3 entry recommendation: ready to start when explicitly requested. The human-label, test and Chroma-decision gates are closed. The two draft defects and qualitative style issue must remain frozen baseline findings; Phase 3 retrieval work must not silently rewrite them or claim to solve them.
