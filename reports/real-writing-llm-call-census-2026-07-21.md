# Real writing LLM call census

Date: 2026-07-21
Task: `3454d80e-02b9-48b6-b160-da614768da9b`

## Result

The fixed log contains 25 HTTP POSTs. Four are the one-and-only draft call for subsections 2.1-2.4; Mandatory Event caused zero retries. The remaining 21 calls are style translation, four handover briefs, four handover extractions, three over-length condensation passes, character-state and relation extraction, one background experience extraction, consistency/continuity checks, two background section reviews, one final section review and one global review.

By call count, draft generation is 4/25 (16%) and auxiliary work is 21/25 (84%). Their token shares cannot be computed because the four streaming draft token samples are unavailable. No calls are proven byte-for-byte duplicates; the review overlap is a consolidation candidate, not permission to delete a consumer.

The logged `39,010` tokens reconcile exactly to Writer `32,442` + ContinuityEditor `263` + Reviewer `6,305`. It is not the full HTTP-call cost: four streaming draft calls have no per-call token usage in the log, and three background-thread calls lost the task context and contributed another known `11,012` tokens outside the logged total. Across calls with usage fields, the recoverable lower bound is `50,022` tokens. Unknown streaming usage is left unavailable rather than estimated.

## Known cost concentration

| Rank | Purpose | Calls | Known tokens | Latency |
|---|---|---:|---:|---:|
| 1 | Review | 4 | 13,800 | 55.8s |
| 2 | Handover extraction | 4 | 9,608 | 27.4s |
| 3 | Length adjustment (all three were condensation) | 3 | 7,732 | 39.5s |

Draft generation is excluded from this ranking because all four streaming token counts are unavailable.

## Next candidates

1. Consolidate background and final section-review ownership. Three section reads account for 11,242 known tokens, while the two background reviews overlap the later synchronous review.
2. Cache handover briefs by normalized input hash. Equal token counts alone are not treated as proof of duplicate input.
3. Test one typed extraction shared by handover and character-state consumers. Their combined known cost is 15,581 tokens, but both downstream contracts must remain intact.

No call was removed, merged or cached in this task.

## Character Arc V2 closeout

`character_arc_contract_v2 = experimental_not_promoted`. V2 configuration was active, but this continuation started directly at `writing`, so no V2 Character Arc Planner ran. Twelve legacy milestones were interpreted through compatibility as soft, with hard=0 and source ID/hash coverage 0/12. V1 would have performed 73 link operations; V2 produced zero edges. This proves only that unsupported legacy edges can be suppressed during continuation, not that V2 planning classifications are good.

Goal completion was approximately 3/4: subsection 4 missed the planned first meeting and repeated subsection 3's scene. Writer tokens changed from 33,084 to 32,442 (-1.94%), which cannot be attributed to V2. Production remains V1; post-check and Mandatory Event remain warning-only. A future V2 validation requires explicit authorization for a new task starting at `character_arcs`.

CMD restore command:

```cmd
set CHARACTER_ARC_CONTRACT_VERSION=v1
```
