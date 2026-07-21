# Writer condense controllable canary integration

Date: 2026-07-21

## Baseline audit

The fixed real task `cd340fcc-1688-40a7-8c28-91e5423ea966` used a 1,000-character target for each of four subsections. Initial output lengths were 1,235, 2,377, 1,934 and 1,498 CJK characters. The existing 1.3 threshold therefore sent the last three subsections through an additional LLM condensation pass.

| Subsection | Target | Initial | Final | Removed | Known tokens | Latency | Tokens/removed char |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1,000 | 1,235 | 1,235 | 0 | 0 | 0.0s | n/a |
| 2 | 1,000 | 2,377 | 2,133 | 244 | 3,812 | 19.5s | 15.623 |
| 3 | 1,000 | 1,934 | 1,591 | 343 | 2,919 | 15.2s | 8.510 |
| 4 | 1,000 | 1,498 | 1,226 | 272 | 2,267 | 11.4s | 8.335 |

The three calls consumed 8,998 known tokens and 46.1 seconds to remove 859 CJK characters, or 10.475 tokens per removed character. The initial chapter was 7,044 characters against a 4,000 target (+76.1%); after condensation it was still 6,185 characters (+54.625%). This establishes a high-cost, low-reduction call path, but does not by itself prove that accepting the longer drafts preserves writing quality.

## Integration

`WRITER_CONDENSE_MODE` now has two modes:

- `legacy` (default): preserves the existing LLM condensation above `target_words * 1.3`;
- `warn`: records that condensation would have occurred, does not construct or call the condensation request, and retains the complete draft.

Invalid values fall back to `legacy` with a configuration warning. Expansion and unfinished-sentence completion are unchanged. Writer prompts, messages, generation parameters, handover, commits, checkpoints and final Review were not modified.

Every subsection emits a `writer_condense_observation` containing only a task hash, location, mode, character counts, ratio, threshold, decision flags, output hash and elapsed time. It contains no draft, prompt or messages.

## Recorded ordering risk

The current Writer extracts the handover and commits some handover effects before `adjust_length`, while the saved draft uses the adjusted text. A legacy condensation can therefore remove or rewrite details after state extraction. This ordering was not changed here because it is a separate behavioral variable.

## Real canary

No Writer or LLM was called during implementation. One normal four-subsection task may be run with:

```cmd
set WRITER_INCREMENTAL_SECTION_REVIEW=false
set WRITER_CONDENSE_MODE=warn
uv run celery -A app.celery_app worker --loglevel=info -P solo -Q writing
```

If the lifecycle matches the baseline, the expected HTTP count is 19 rather than 22. A different lifecycle must be assessed by the number of omitted condense calls, not by forcing an exact total.

Promotion requires zero condense calls, no new generation retries, a completed task with valid draft/checkpoint/final Review, acceptable total length and a user judgment that the draft remains usable. `warn` is not authorized as the production default until that run passes.

Engineering verification: 59 targeted tests passed, affected-module compileall passed, and implementation-time Writer/LLM calls were zero.
