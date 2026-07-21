# WriterExecutionContract Canary attribution and V1.1 closure

Date: 2026-07-22

## Outcome

The real canary completed safely, but the execution-contract route is **not eligible for another demo**. V1 injected into two of four subsections. After the permitted V1.1 observability and wording changes, only S1.4 remains below the frozen 450-token cap. Production remains `WRITER_EXECUTION_CONTRACT_MODE=off`.

No Writer or LLM call was made during this work. The task database was opened read-only and the existing production provider reconstructed the four contracts from the stored outline.

## Real canary

Task `019fc0c8-1a20-4be3-8252-54aaf1c5aa27` completed with four main draft calls, zero Mandatory Event retries and zero Condense calls. It used 36,387 reported tokens in 351.2 seconds. The chapter contains about 8,250 characters against an aggregate target near 4,000.

| Subsection | V1 contract | V1 tokens | Output characters | Target ratio |
|---|---|---:|---:|---:|
| S1.1 | fallback | 796 | 1,256 | 1.256 |
| S1.2 | fallback | 488 | 2,272 | 2.272 |
| S1.3 | injected | 405 | 1,592 | 1.592 |
| S1.4 | injected | 248 | 3,095 | 3.095 |

The successful S1.3/S1.4 injections did not establish length control. Mandatory Event warnings are not treated as quality truth because that detector remains literal and warning-only.

## Exact budget attribution

| Subsection | Objective | Events | Prohibited | Length | Stop | Wrapper | V1.1 total | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| S1.1 | 283 | 283 | 24 | 89 | 176 | 33 | 888 | fallback |
| S1.2 | 57 | 178 | 24 | 89 | 194 | 37 | 579 | fallback |
| S1.3 | 53 | 191 | 24 | 89 | 102 | 37 | 496 | fallback |
| S1.4 | 30 | 77 | 24 | 89 | 82 | 37 | 339 | injected |

All figures are estimated tokens from the current project estimator. Confirmed-continuity cost is zero in all four contracts.

S1.1 has only one required event but still reaches 888 tokens because its 214-character description is rendered as both the 283-token objective and the 283-token required event. Its full next-subsection boundary plus explicit stop instruction costs another 176 tokens. The allowed deduplication scope does not permit deleting either typed objective/event or truncating the future-event boundary.

S1.3 and S1.4 retain their semantic hashes exactly:

- `f3a74abfeaf7c33d34de21c74cec80a6f4cf13476f77133ceab5b93f9b4c77cc`
- `b737888054bf25b37bd58052e867cc2740302de6cedc303a8ca0079e21fc4e9b`

Source ID/hash traceability is 100% for all reconstructed contracts.

## Prompt conflicts

- `duplicated_contract_content`: description/key points already appear in the legacy outline, mandatory-event block and progress context, then reappear in the appended contract.
- `verbose_objective`: S1.1's objective alone costs 283 tokens.
- `verbose_stop_boundary`: S1.1-S1.3 embed the complete next-subsection event list.
- `overplanned_event_density`: S1.2-S1.4 allocate only 200-250 target characters per required item before transitions and prose.
- `conflicting_length_instruction`: the style brief asks for roughly 400 characters per paragraph while the subsection target is about 1,000 total characters and the same Prompt requests multiple events, gradual pacing and sensory detail.
- `weak_total_length_wording`: V1 did not explicitly say the range applied to the whole subsection.
- `insufficient_evidence`: these conflicts prevent attributing the overrun solely to model instruction noncompliance.

## V1.1 change

V1.1 preserves attempted token totals and a component ledger even when validation falls back. Render-time deduplication is limited to normalized exact repetition; typed fields, source manifests and semantic hashes remain unchanged. The length instruction now explicitly applies to the entire subsection, asks the Writer to compress depiction rather than add people/scenes/future events, and places an immediate-stop instruction at the end.

The token cap remains 450. No required event was removed, no source was truncated, and generation parameters, retry, checkpoint, SceneSpec semantics and the Writer Prompt body are unchanged.

## Decision

The gate fails: only 1/4 real contracts fits the cap, all-four offline injection is false, and the Prompt still contains an upstream paragraph-length/event-density conflict. Running the same canary again would not answer a new question.

Keep:

```bat
set WRITER_EXECUTION_CONTRACT_MODE=off
```

No further real demo is authorized. The only recommended next direction is a separate outline event-density audit; it must not be bundled with another execution-contract change.

Targeted verification: 28 tests passed; affected-module `compileall` passed. No Writer or LLM call was made.
