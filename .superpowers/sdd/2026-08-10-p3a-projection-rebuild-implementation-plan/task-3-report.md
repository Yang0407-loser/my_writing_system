# Task 3 Report: Allocate Canon stream positions and atomically fan out Deliveries

## Status

Task 3 is complete. The recovered implementation was audited in place, independently verified, and kept within the Task 3 scope.

## Recovered TDD evidence

The controller supplied the following historical RED evidence from the prior implementer. It is preserved here as historical evidence rather than represented as a fresh run by the recovery owner:

- Before production changes, the focused suite reported **14 failed / 8 passed**.
- Failures were caused by the intentionally missing `stream_position`, `next_stream_position`, `ProjectionDelivery`, and baseline partition behavior.
- Tests were added first for:
  - seven Envelope-to-Delivery rows with matching IDs and aligned project stream positions;
  - a registered disabled projector receiving no historical or new Envelope until activation, then receiving only post-activation work;
  - rollback after `after_stream_position`, `after_outbox`, and `after_projection_deliveries`;
  - seven baseline active partitions and an initial counter of zero;
  - eight concurrent PostgreSQL commits allocating strict positions `1..8`;
  - v0 snapshot restore creating Deliveries only for actual imported Envelopes.

No new defect was discovered during recovery, so no additional production change or new RED/GREEN cycle was needed.

## Implemented behavior

- `CanonicalRepository.create_project()` initializes `next_stream_position=0` and exactly seven active baseline `ProjectionPartition` rows with cursor zero and `activation_after_position=0`.
- `CanonicalRepository.next_stream_position(project)` increments and flushes the project-local counter on the already locked project row without committing the caller's transaction.
- `CanonicalCommitService` accepts `projector_registry=DEFAULT_PROJECTOR_REGISTRY`.
- A commit locks the project row, allocates one stream position, assigns it to the Canon Commit and every Envelope and Delivery created by that commit, and commits all state atomically.
- Fan-out uses the exact required predicate: the projector is in the supplied registry, its project partition exists and is active, and the allocated position is greater than `activation_after_position`.
- One `ProjectionDelivery` is created per Envelope, aligned by `outbox_event_id`, projector, barrier kind, and stream position.
- Failure hook stages `after_stream_position` and `after_projection_deliveries` were added; existing `after_outbox` covers failure after Envelope insertion.
- Snapshot export includes Deliveries and Partitions. Import remains schema-version v0 compatible; a genuine pre-P3A snapshot lacking the project counter and Commit/Envelope positions derives positions from the canonical state chain, creates one Delivery only for each imported Envelope, and derives continuous published cursors without synthesizing missing historical Envelopes.

## Fresh verification

### Exact focused Task 3 suite

Command:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_commit_service.py tests\unit\canonical\test_repositories.py tests\integration\canonical\test_commit_concurrency.py tests\integration\canonical\test_snapshot_restore.py -q
```

Result:

```text
....................sssss..                                              [100%]
22 passed, 5 skipped in 6.40s
```

The five skipped tests are the PostgreSQL concurrency group, which was then run separately against a real PostgreSQL 16 container.

### Ruff

Command:

```powershell
& .\.venv\Scripts\python.exe -m ruff check app\canonical\repositories.py app\canonical\commit_service.py app\canonical\snapshot.py tests\unit\canonical\test_commit_service.py tests\unit\canonical\test_repositories.py tests\integration\canonical\test_commit_concurrency.py tests\integration\canonical\test_snapshot_restore.py
```

Result:

```text
All checks passed!
```

### Real PostgreSQL concurrency gate

Lifecycle:

- Docker Desktop initially was not running; the CLI reported `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.` Docker Desktop was started successfully.
- Verified that no existing container matched the unique name before creation.
- Created disposable container `p3a-task3-recover-20260812-a7c91` from `postgres:16-alpine`.
- Created database `p3a_task3_a7c91_test`, satisfying the fixture's `_test` suffix guard.
- Docker assigned ephemeral host port `127.0.0.1:14801`.
- Verified before testing:
  - container ID `9fbbc21515cad34b97f6a9a622a9eaf3c4844dcdc33100799c6feaf6031d7cfc`;
  - name `/p3a-task3-recover-20260812-a7c91`;
  - image `postgres:16-alpine`;
  - state `running`;
  - `pg_isready`: accepting connections.

Command:

```powershell
$env:TEST_CANONICAL_DATABASE_URL='postgresql+psycopg://p3a_task3:p3a_task3_pw@127.0.0.1:14801/p3a_task3_a7c91_test'
& .\.venv\Scripts\python.exe -m pytest tests\integration\canonical\test_commit_concurrency.py -q
```

Result:

```text
.....                                                                    [100%]
5 passed in 3.79s
```

This includes the eight-concurrent-commit test asserting strict positions `1..8`, a project counter of eight, and 56 one-to-one aligned Envelopes and Deliveries.

Before cleanup, the exact same container ID/name/image/running state were re-verified. Only `p3a-task3-recover-20260812-a7c91` was removed with `docker rm -f`; Docker confirmed that name.

## Broader-suite concerns

Command:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical -q
```

Result:

```text
6 failed, 76 passed, 3 errors in 12.03s
```

The known downstream concerns were reproduced and intentionally not changed because they lie outside Task 3's allowed files/cutover:

- Five legacy Dispatcher/Barrier failures:
  - three in `test_outbox_dispatcher.py`;
  - two in `test_projection_barrier.py`.
  These components still mutate/read Envelope scheduling status while Task 3 establishes Delivery as the scheduling authority; their cutover is planned in later work.
- `test_schema_v0.py::test_downgrade_and_upgrade_are_repeatable` also fails, and three schema test fixtures error, because SQLite attempts to execute PostgreSQL-only `DO $$` SQL from migration `0003_p3a_projection_backfill.py`.

No Task 3 production or test file was broadened to hide these pre-existing/downstream failures.

## Files changed

- `app/canonical/repositories.py`
- `app/canonical/commit_service.py`
- `app/canonical/snapshot.py`
- `tests/unit/canonical/test_commit_service.py`
- `tests/unit/canonical/test_repositories.py`
- `tests/integration/canonical/test_commit_concurrency.py`
- `tests/integration/canonical/test_snapshot_restore.py`
- `.superpowers/sdd/2026-08-10-p3a-projection-rebuild-implementation-plan/task-3-report.md`

## Self-review

- **Transaction atomicity:** allocation, Commit, Revision, StateVersion, Ledger, Envelope, Delivery, head movement, and idempotency completion all use the service-owned transaction; no repository helper commits.
- **Rollback including counter:** failures after allocation, Envelope flush, and Delivery flush roll back all tracked Canon rows, both heads, Envelopes, Deliveries, the idempotency reservation, and `next_stream_position`.
- **Concurrency:** the project row is selected `FOR UPDATE` before increment; the schema's unique `(project_id, stream_position)` Commit constraint provides a second invariant. Real PostgreSQL concurrency produced no gaps or duplicates.
- **Fan-out:** only specs present in the supplied registry and an active project partition with `stream_position > activation_after_position` receive work. Disabled projectors receive neither history nor commits before activation.
- **Snapshot compatibility:** new snapshots carry Delivery/Partition state. Old v0 snapshots derive missing positions from Canon state ancestry, restore Deliveries only from present Envelopes, preserve published/pending evidence, and derive only continuous cursors.
- **Scope:** the recovered dirty diff contained exactly the seven brief-authorized Task 3 files. The report is the only additional required artifact. `git diff --check` passed.

## Fix Round 1

### Findings addressed

1. The still-public `OutboxDispatcher` and `ProjectionBarrier` compatibility path now uses `ProjectionDelivery` as its only mutable scheduling authority. The dispatcher selects eligible Deliveries joined to immutable Envelopes and committed Canon rows, constructs the current `ProjectionMessage` contract (`projection_event_id`, Envelope and Delivery IDs, revision/state IDs, projector ID, and stream position), records synchronous compatibility success/failure only on the Delivery, and leaves every Envelope scheduling field unchanged. This intentionally does not add leases, attempt-ledger rows, retry policy, dead-letter policy, or a worker. The barrier evaluates critical Delivery state rather than Envelope state.
2. `CanonicalCommitService.commit()` now locks the scoped project row before inserting or flushing the idempotency reservation. Duplicate replay and new reservation paths therefore both run under the project lock while retaining one atomic rollback boundary.
3. Pre-P3A v0 snapshot restore now synthesizes deterministic UUIDv5 Delivery IDs from tenant/project/Envelope/projector identity and deterministic UUIDv5 Partition IDs from tenant/project/projector identity. Synthesized timestamps are copied from source Envelope/Project rows, so two independent restores have identical synthesized identities and byte-identical re-exports.

### RED evidence

Dispatcher/Barrier production RED after making the two test modules independently collectible:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_outbox_dispatcher.py tests\unit\canonical\test_projection_barrier.py -q
```

```text
FFF.F.F                                                                  [100%]
5 failed, 2 passed in 2.77s
```

The failures showed successful projectors counted as failed because the dispatcher constructed obsolete `ProjectionMessage` fields, and the barrier remained failed after critical dispatch because it read immutable Envelope status.

Lock-ordering and deterministic-restore RED were captured together with the initial Dispatcher/Barrier collection attempt:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_outbox_dispatcher.py tests\unit\canonical\test_projection_barrier.py tests\unit\canonical\test_commit_service.py::test_project_is_locked_before_idempotency_reservation tests\integration\canonical\test_snapshot_restore.py::test_v0_restore_synthesizes_stable_projection_identity_and_reexport -q
```

```text
EEEEEEEFF                                                                [100%]
2 failed, 7 errors in 1.53s
```

The two behavioral failures were exact:

- `test_project_is_locked_before_idempotency_reservation`: at `after_reservation`, observed `locked is False`.
- `test_v0_restore_synthesizes_stable_projection_identity_and_reexport`: two restored snapshot byte streams differed.

The seven errors were only pytest fixture discovery in the two legacy test modules, not production behavior. Importing the existing sibling fixture by name made those modules independently runnable; the clean Dispatcher/Barrier RED above then exposed the five production failures.

### GREEN evidence

Targeted regression gate after minimal fixes:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_outbox_dispatcher.py tests\unit\canonical\test_projection_barrier.py tests\unit\canonical\test_commit_service.py::test_project_is_locked_before_idempotency_reservation tests\integration\canonical\test_snapshot_restore.py::test_v0_restore_synthesizes_stable_projection_identity_and_reexport -q
```

```text
.........                                                                [100%]
9 passed in 3.75s
```

Complete requested Fix Round 1 gate:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_commit_service.py tests\unit\canonical\test_repositories.py tests\integration\canonical\test_commit_concurrency.py tests\integration\canonical\test_snapshot_restore.py tests\unit\canonical\test_outbox_dispatcher.py tests\unit\canonical\test_projection_barrier.py -q
```

```text
.....................sssss..........                                     [100%]
31 passed, 5 skipped in 9.98s
```

The five skips are the unchanged real-PostgreSQL concurrency group. Fix Round 1 did not alter PostgreSQL-specific behavior, so the earlier fresh PG16 result of `5 passed in 3.79s` remains the retained real-PostgreSQL evidence as requested.

Ruff command:

```powershell
& .\.venv\Scripts\python.exe -m ruff check app\canonical\repositories.py app\canonical\commit_service.py app\canonical\snapshot.py app\canonical\outbox.py app\canonical\projection_barrier.py tests\unit\canonical\test_commit_service.py tests\unit\canonical\test_repositories.py tests\integration\canonical\test_commit_concurrency.py tests\integration\canonical\test_snapshot_restore.py tests\unit\canonical\test_outbox_dispatcher.py tests\unit\canonical\test_projection_barrier.py
```

```text
All checks passed!
```

### Files changed in Fix Round 1

- `app/canonical/outbox.py`
- `app/canonical/projection_barrier.py`
- `app/canonical/commit_service.py`
- `app/canonical/snapshot.py`
- `tests/unit/canonical/test_outbox_dispatcher.py`
- `tests/unit/canonical/test_projection_barrier.py`
- `tests/unit/canonical/test_commit_service.py`
- `tests/integration/canonical/test_snapshot_restore.py`
- `.superpowers/sdd/2026-08-10-p3a-projection-rebuild-implementation-plan/task-3-report.md`

### Remaining concerns

- The compatibility dispatcher is deliberately synchronous and minimal. Task 5 remains responsible for leases, retry/dead-letter policy, and durable attempt evidence; Task 6 remains responsible for worker behavior.
- The unrelated SQLite schema tests still attempt to execute PostgreSQL-only migration `0003`; Fix Round 1 does not change migration scope.
- The replay-result Delivery count/`outbox_event_ids` minor finding remains deferred in the controller ledger and was not changed in this round.

Fresh broader canonical unit verification:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical -q
```

```text
1 failed, 82 passed, 3 errors in 12.85s
```

All five previously failing Dispatcher/Barrier tests now pass. The sole failure and three setup errors are confined to `test_schema_v0.py` and have the same out-of-scope root cause: SQLite executes PostgreSQL-only `DO $$` SQL in migration `0003_p3a_projection_backfill.py`.

## Fix Round 2

### Findings addressed

1. `ProjectionBarrier.ensure_ready()` now enumerates the complete tenant/project/commit-scoped critical Envelope set and left-joins corresponding Delivery state. A required critical Envelope with no Delivery produces a missing state and fails closed instead of allowing the remaining published Deliveries to make the barrier falsely ready.
2. `_stable_snapshot_id()` now encodes its namespace, kind, and identity components as a canonical JSON array using `canonical_json_bytes()` before UUIDv5 hashing. This preserves deterministic IDs while making component boundaries unambiguous even when arbitrary identity strings contain colons.

### RED evidence

Command:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_barrier.py::test_missing_critical_delivery_fails_closed tests\integration\canonical\test_snapshot_restore.py::test_stable_snapshot_ids_use_unambiguous_component_encoding -q
```

Result:

```text
FF                                                                       [100%]
2 failed in 0.77s
```

Exact failures:

- deleting one of three critical Deliveries after the remaining critical Deliveries were published returned `ready` instead of `failed`;
- `_stable_snapshot_id("partition", "tenant:a", "project", "analytics")` equaled `_stable_snapshot_id("partition", "tenant", "a:project", "analytics")`.

The first GREEN attempt exposed an import typo (`canonical_json` is not exported by the hashing module). The implementation was corrected to use the existing public `canonical_json_bytes()` serializer; no hashing API was changed.

### GREEN evidence

Targeted command:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_barrier.py::test_missing_critical_delivery_fails_closed tests\integration\canonical\test_snapshot_restore.py::test_stable_snapshot_ids_use_unambiguous_component_encoding -q
```

Result:

```text
..                                                                       [100%]
2 passed in 0.65s
```

Complete requested Task 3 plus Dispatcher/Barrier gate:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_commit_service.py tests\unit\canonical\test_repositories.py tests\integration\canonical\test_commit_concurrency.py tests\integration\canonical\test_snapshot_restore.py tests\unit\canonical\test_outbox_dispatcher.py tests\unit\canonical\test_projection_barrier.py -q
```

```text
.....................sssss............                                   [100%]
33 passed, 5 skipped in 9.92s
```

The five skips remain the unchanged PostgreSQL concurrency tests; Fix Round 2 changes neither allocation nor PostgreSQL-specific behavior.

Ruff result for the complete Fix Round 2 gate file set:

```text
All checks passed!
```

`git diff --check` also passed.

### Files changed in Fix Round 2

- `app/canonical/projection_barrier.py`
- `app/canonical/snapshot.py`
- `tests/unit/canonical/test_projection_barrier.py`
- `tests/integration/canonical/test_snapshot_restore.py`
- `.superpowers/sdd/2026-08-10-p3a-projection-rebuild-implementation-plan/task-3-report.md`

### Remaining concerns

- The deferred replay Minor remains unchanged by instruction.
- The Task 2 SQLite/PostgreSQL migration debt remains unchanged by instruction.
- Task 5/6 continue to own lease/retry/dead-letter/worker behavior; this round changes only barrier completeness and deterministic snapshot identity encoding.
