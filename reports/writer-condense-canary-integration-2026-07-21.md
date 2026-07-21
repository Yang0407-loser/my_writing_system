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

`WRITER_CONDENSE_MODE` has two modes:

- `warn` (default after the real canary): records that condensation would have occurred, does not construct or call the condensation request, and retains the complete draft;
- `legacy`: preserves the existing LLM condensation above `target_words * 1.3` and remains the explicit rollback path.

Invalid values fall back to `legacy` with a configuration warning. Expansion and unfinished-sentence completion are unchanged. Writer prompts, messages, generation parameters, handover, commits, checkpoints and final Review were not modified.

Every subsection emits a `writer_condense_observation` containing only a task hash, location, mode, character counts, ratio, threshold, decision flags, output hash and elapsed time. It contains no draft, prompt or messages.

## Recorded ordering risk

The current Writer extracts the handover and commits some handover effects before `adjust_length`, while the saved draft uses the adjusted text. A legacy condensation can therefore remove or rewrite details after state extraction. This ordering was not changed here because it is a separate behavioral variable.

## Real canary

No Writer or LLM was called during implementation. The separately executed real task `4ce7e82f-d3b4-44a6-8dcf-ec1e638d77e9` then exercised `warn` on four normal subsections:

- 19 HTTP calls, including exactly four main draft calls;
- all four subsections exceeded the legacy threshold, and all four retained their original draft with zero condense calls;
- zero Mandatory Event retries and zero incremental section reviews;
- final section Review and global Review both completed;
- task status, saved draft and checkpoint all reached `completed`;
- original subsection lengths were 1,770, 1,895, 1,607 and 1,658 characters (6,930 total); persisted output reported 6,965 words/characters;
- the user judged the resulting draft `usable`.

The real acceptance conditions therefore passed and `warn` is promoted to the default. The run's 36,599 task tokens and 332.6 seconds are recorded for observability, but cross-run token and latency differences are not claimed as exact causal savings because generated content differed. The directly attributable result is the omission of four would-have-condensed calls.

Normal CMD startup may state the promoted mode explicitly:

```cmd
set WRITER_INCREMENTAL_SECTION_REVIEW=false
set WRITER_CONDENSE_MODE=warn
uv run celery -A app.celery_app worker --loglevel=info -P solo -Q writing
```

If the lifecycle matches the baseline, the expected HTTP count is 19 rather than 22. A different lifecycle must be assessed by the number of omitted condense calls, not by forcing an exact total.

To roll back in CMD:

```cmd
set WRITER_CONDENSE_MODE=legacy
uv run celery -A app.celery_app worker --loglevel=info -P solo -Q writing
```

Engineering verification: 59 targeted tests passed, affected-module compileall passed, and implementation-time Writer/LLM calls were zero.
