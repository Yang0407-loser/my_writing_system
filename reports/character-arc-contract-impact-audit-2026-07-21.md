# Character Arc Contract Impact Audit

Date: 2026-07-21

## Decision

Contract V2 implementation is justified because character-arc milestones reach Writer twice: through the subsection arc context and through EventGraph/pre-check. EventGraph relations also participate in causal RAG expansion and handover extraction. Arc post-check remains warning-only and causes no retry.

Production remains `CHARACTER_ARC_CONTRACT_VERSION=v1` by default. Legacy checkpoints are read through a soft compatibility view and are not rewritten.

## Fixed Tasks

| Task | Availability | Milestones | V1 link operations | Same-section pairwise | Proven causal edges | V2 legal existing edges |
|---|---|---:|---:|---:|---:|---:|
| `e7cb9ac2` | available | 15 | 115 | 105 | 0 | 0 |
| `b5ddb41c` | available | 12 | 73 | 66 | 0 | 0 |

Totals: 27 legacy milestones and 188 link operations, including 171 same-section pairwise operations. Proven causal edges: 0.

The legacy schema has no before/trigger/after transition fields or provenance. Therefore 0 milestones are structurally provable as hard and 27 remain unresolved for evidence classification. Compatibility treats unclassified legacy milestones as soft; it does not reuse previous human/Codex evaluation labels.

## Production Impact

- `CharacterFormatter.build_arc_context` injects subsection milestones into Writer messages.
- `EventGraph.query_relevant` and `pre_check` append the same planning material to event context; V1 treats every arc milestone as mandatory.
- `expand_causal` previously expanded every same-section event even without an explicit edge.
- `_extract_handover` sends up to ten arc events to a separate handover LLM call.
- `post_check` only logs warnings and does not retry, rollback, or block output.
- `StateCommitter` can update EventGraph state after generation.

## V2 Contract

- Only complete, sourced state changes can remain `hard_arc_transition`; maximum two per character per chapter/section.
- `soft_arc_progress` is non-mandatory reference context.
- observational, ordinary plot, unsupported, and unresolved milestones are not injected as arc events.
- Edges require explicit causality/dependency or an exact hard-state chain.
- Same-section position alone never creates an edge or causal expansion.

## Limits

No Writer or LLM was called. The duplicate/redelivered `6d8187a1...` task was excluded from cost evidence. Legacy data cannot validate how a newly generated V2 plan will distribute classifications; that requires one separately authorized real Demo.

## Next Demo

If directed tests pass, run one task with `CHARACTER_ARC_CONTRACT_VERSION=v2`. Observe edge and classification counts, Writer calls/tokens, arc warnings, subsection goal completion, and whether the draft is easier to continue. Do not use frozen Precision/Recall as the primary conclusion.
