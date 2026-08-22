# P0 Task 2 Contract and Provenance Recovery

> Date: 2026-08-09
> Branch: `feat/p0-p2-foundation`
> Baseline commit: `876c4a9`

## Outcome

Task 2 is Green within its defined scope. It fixes the Scene Reality prompt
fixture compatibility break, freezes the deployed `Writer.run` public
signature, and removes R3.4/R3.5/R3.6 experiment coupling to mutable live code
and untracked runtime output.

This result does not approve the overall P0 Gate. The full Foundation suite
still contains independently classified failures owned by later tasks.

## Red evidence

The original workspace, where historical private/runtime artifacts were still
present, reproduced the plan's failures:

```text
Scene Reality: 2 failed, 13 passed
Writer public signature: 1 failed
R3.4/R3.5/R3.6: 25 failed, 13 passed
```

The Writer Boundary failures all terminated at the same invalid coupling: the
historical manifests expected the old `llm_client.py` bytes while the builders
hashed the current live file.

A clean worktree additionally showed that the experiments depended on
untracked `outputs/` files. New contract tests were added first and failed
because all three generations of source pins resolved outside repository
fixtures.

## Implementation

- `anti_ai_expression_constraints=""` is supplied only in the historical
  Scene Reality prompt fixture. Production prompt behavior is unchanged.
- Tests that require the secret-bearing original task-state now skip explicitly
  when it is unavailable. Prompt assembly is exercised with non-sensitive
  synthetic inputs, so the current template contract remains covered in clean
  worktrees.
- `rag_metadata_provider` is recorded as the final optional, read-only
  `Writer.run` parameter. No other parameter name, order, annotation, or
  default changed.
- Historical Writer Boundary inputs were copied byte-for-byte into
  `experiments/writer_boundary_v12_shared/fixtures/` and builders now read only
  those version-controlled fixtures.
- `.gitattributes` marks the provenance fixtures as binary so Windows checkout
  cannot rewrite LF/CRLF bytes and invalidate their hashes.
- The pinned `llm_client.py` was recovered from Git commit
  `ee46ab5607e13ee62b7763b0c6c84fc936d703ab`.
- R3.6 fake-provider tests now inject deterministic, non-secret runtime
  settings. Production credential validation remains fail-closed.

## Provenance proof

```text
expected manifest SHA-256:
2bfd086bcf00f1275accfe37b9b881fb8be64edb6279805d18678c9033a67c39

recovered fixture SHA-256:
2bfd086bcf00f1275accfe37b9b881fb8be64edb6279805d18678c9033a67c39
```

All pre-existing manifest hash values remain unchanged. The archived
experiments therefore continue to describe the bytes they originally pinned;
they do not pretend that current live code produced historical results.
All ten working-tree fixtures and their staged Git blobs were independently
hashed and matched their expected values.

## Verification

Clean-worktree Task 2 focused Gate, without private files:

```text
55 passed, 2 skipped, 1 warning in 1.54s
```

The two skips are the explicit historical-input assertions described above.
The only focused warning is the existing `jieba/pkg_resources` deprecation.

The exact complete command still stops at the previously recorded collection
blocker:

```text
tests/unit/test_world_runtime_reviewer_real_task_side_by_side.py
ModuleNotFoundError:
experiments.world_runtime_writer_canary.wr310_reviewer_real_task_side_by_side
```

Ignoring only that node produced the final clean-worktree diagnostic below:

```text
1396 passed, 124 failed, 17 skipped, 6 warnings in 118.93s
```

Compared with Task 1's `1308 passed / 156 failed / 15 skipped` unit baseline
plus 55 passing integration nodes, every Task 2 failure cluster has cleared.
The remaining failures are dominated by realization-policy credential setup,
World Runtime private/golden artifacts, earlier Writer Boundary R2-R3.4 chains,
decision-shadow fixtures, and sparse-kernel source pins.

## Security and Gate interpretation

- No API key value was copied or recorded.
- The private Scene Reality task-state was not added to Git.
- A secret-pattern scan of shared JSON fixtures found no credential material.
  The historical Python snapshot contains API-key variable names and handling
  code, but no credential value.
- Task 2 focused Gate: Green.
- Complete P0 Gate: Red; proceed only through the remaining Foundation tasks.
