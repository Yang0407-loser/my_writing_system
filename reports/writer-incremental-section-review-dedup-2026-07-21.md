# Writer incremental section review deduplication

Date: 2026-07-21

## Consumer audit

The incremental review inside `Writer.run` writes only transient `blackboard.section_reviews` entries. The status API exposes them and the frontend uses them as an in-progress display. No generation, retry, revision, subsection progression, StateCommitter, checkpoint, final-score or task-success path reads those results.

Final review is independent. `coordinator._phase_review` creates a fresh Reviewer, calls `review_section` from the committed `section_texts`, builds its own `section_reviews`, and then calls `review_global`. It never reads the Writer's incremental entries. The background experience extraction is also independent and remains unchanged.

The incremental review can therefore be disabled without fabricating replacement scores. The frontend may simply have no intermediate score before the final review is available.

## Implementation

`WRITER_INCREMENTAL_SECTION_REVIEW` defaults to `false`:

- false: return before importing or constructing Reviewer and before starting its thread;
- true/1/yes: preserve the legacy trigger and BlackBoard pending/done behavior;
- invalid values: behave as false and produce a configuration warning.

The guard changes no Writer messages, prompt construction, generation parameters, returned draft, commit order, checkpoint contract or final review semantics. Experience extraction remains in its original separate thread.

Observability records only a task ID hash, section/subsection, enabled/started booleans, bounded skip reason and `production_effect=false`. It does not contain prose, messages, prompts or review input.

## Cost expectation

The fixed baseline had two incremental section reviews:

| Call | Known tokens | Latency |
|---|---:|---:|
| 13 | 3,713 | 7.4s |
| 18 | 3,782 | 7.7s |
| Total | 7,495 | 15.1s |

For another four-subsection task with the same call shape, HTTP POST count should fall from 25 to 23. The official 39,010-token log did not include these background calls, so a change in that total is not an acceptance criterion.

## Real-run verification

The follow-up task `cd340fcc-1688-40a7-8c28-91e5423ea966` completed four subsections and passed the functional acceptance check:

- 4 main draft calls and 0 actual Mandatory Event retries;
- 4 `disabled_by_config` incremental-review observations and 0 incremental reviews started;
- no transient `blackboard.section_reviews` field;
- 1 experience extraction call, with 9 new event rows persisted for the task;
- 1 final section review and 1 global review;
- saved draft, `completed` task status, and a checkpoint at phase `completed`.

The log contains 22 HTTP POSTs, rather than the same-shape estimate of 23. This is not an additional one-call saving attributable to review deduplication. The baseline was a continuation starting at writing, while this run was a new first chapter: it omitted 4 handover-brief calls and added 3 character-arc/world-state calls. The count reconciles as `25 - 2 incremental reviews - 4 handover briefs + 3 upstream calls = 22`.

The run logged 44,307 task-context tokens, including Writer 32,595 and Reviewer 6,369. These totals must not be compared directly with the baseline 39,010 because the inputs and lifecycle differ, and background calls are excluded from both task-context counters. The real run proves that the two unwanted call sites no longer execute; it does not provide an exact same-input token-saving measurement. The 7,495-token and 15.1-second figures remain baseline-derived expected savings for the two removed calls.

Engineering verification: 49 targeted tests passed, affected-module compileall passed, and Writer/LLM calls were zero.

For a different subsection count, verify removed incremental calls rather than requiring exactly 23 total calls.

CMD optimized mode:

```cmd
set WRITER_INCREMENTAL_SECTION_REVIEW=false
uv run celery -A app.celery_app worker --loglevel=info -P solo -Q writing
```

Legacy compatibility mode:

```cmd
set WRITER_INCREMENTAL_SECTION_REVIEW=true
```

The optimization is accepted on one real task. No handover cache or shared extraction optimization has started.
