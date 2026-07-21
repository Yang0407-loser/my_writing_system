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

## One real-run check

No model was called during implementation. Run one normal real writing task after restarting the worker. Verify one draft call per subsection, zero Mandatory Event retries, zero incremental review calls, retained experience extraction, one final section review, one global review, a valid checkpoint, saved draft and `completed` status.

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

No handover cache or shared extraction optimization has started.
