# Character State Update effectiveness audit

Date: 2026-07-21

## Outcome

The engineering gate did not pass, so no `off` mode or production change was implemented. Four real calls across four tasks were recovered without invoking Writer or another LLM. Two calls produced source-supported, substantive state transitions for all five characters; two calls remain `unavailable` because the raw model response or reliable pre-section state was not persisted.

Disabling `CharacterManager.update_states` is therefore not justified. Confirmed substantive calls are 2/4 (50%) across all samples and 2/2 (100%) among evaluable samples, rather than below the required 20%. Confirmed nonproductive calls are 0/4, rather than at least 80%. The unchanged final state in one task is not counted as a no-op because the missing raw response makes valid unchanged output indistinguishable from parse or ID-matching failure.

## Cost ledger

| Sample | Section | Characters | Input | Output | Known total | Latency | Classification |
|---|---:|---:|---:|---:|---:|---:|---|
| `536ef970…` | 1 | 5 | 6,187 | 364 | 6,551 | 8.3s | substantive transition |
| `2fda0a9c…` | 1 | 5 | 6,375 | 396 | 6,771 | 6.6s | substantive transition |
| `dfaffbe8…` | 1 | 5 | 6,410 | 339 | 6,749 | 4.5s | unavailable |
| `5802b4a3…` | 2 | 5 | 5,578 | 395 | 5,973 | 6.4s | unavailable |

The four calls used 26,044 known tokens and 25.8 seconds, averaging 6,511 tokens and 6.45 seconds. Ten of twenty character results are confirmed substantive; the other ten are unavailable. Inclusive cost per confirmed substantive character update is 2,604.4 tokens. Turning the feature off would theoretically remove all 26,044 tokens in these samples, but that is not a safe or realized saving.

## Consumer audit

`update_states` runs synchronously after all subsections in each section. It deep-copies the arcs, sends matched character IDs and current/starting/ending states with up to 8,000 characters of section prose, then accepts only non-empty `current_state` values whose `character_id` exactly matches an existing arc. Parse errors are silently ignored.

The result does not control draft retries, subsection commits or task success. It can affect later sections within the same `Writer.run`, because the Writer-local variable is updated before the next section. The status API also exposes the updated Blackboard value.

The audit found a separate propagation defect: `Writer.run` does not return its updated `character_arcs`. Coordinator therefore keeps the pre-update object for state, checkpoint and final Reviewer input. In all three tasks where Blackboard and checkpoint differed, the checkpoint still had `current_state == starting_state` for every character. This means meaningful updates can affect a later section in the same run and the live status API, but are lost from the completed checkpoint and final review path.

That defect is recorded but not repaired here. Repairing propagation while deciding whether to disable extraction would change a second core variable and violate the task boundary.

## Decision

- Historical evidence is sufficient to reject the proposed disable gate, not to approve it.
- `WRITER_CHARACTER_STATE_UPDATE_MODE` was not added.
- Production behavior remains the existing legacy call.
- No new Demo is requested by this audit.
- Shared Typed Post-Write Extraction remains off and not promoted.
- The only follow-up requiring a separate decision is whether to repair the Writer-to-Coordinator state propagation defect while retaining the current extractor.

No full prose, full state text, Prompt, messages, API key, database content or Redis export is included in this report.
