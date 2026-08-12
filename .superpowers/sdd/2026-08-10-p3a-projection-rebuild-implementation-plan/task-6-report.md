# Task 6 Report: Fenced Projection Worker

## Outcome

Implemented deterministic projection maintenance fences, a shared Delivery-based
Projection Worker, and the temporary Outbox compatibility facade. PostgreSQL
`ProjectionDeliveryStore` remains the sole claim/lease/outcome authority; no
Celery event identity or `OutboxEvent.status` is used for eligibility.

## RED / GREEN evidence

- RED: the three new Task 6 test modules failed collection because
  `app.canonical.projection_worker` and `app.canonical.projection_locks` did not
  exist.
- RED: compatibility tests then failed because direct Outbox dispatch created no
  `ProjectionAttempt`, left Outbox mirrors unchanged, and bypassed the Delivery
  worker.
- GREEN: Task 6 unit + real PostgreSQL gate passed `15 passed`.
- Fresh final Task 6 + affected facade verification passed `19 passed`; Ruff
  passed all touched Task 6 files and `git diff --check` exited zero.

## Advisory fencing

- `ProjectionLockScope` identifies `tenant_id/project_id/projector_id`.
- SHA-256 of the slash-separated scope supplies two signed int32 advisory keys.
- Worker writes use session-level `pg_advisory_lock_shared`; maintenance uses
  session-level `pg_advisory_lock`.
- Both modes release in `finally`; connection close/loss provides PostgreSQL's
  final lock release guarantee.
- Real PostgreSQL race coverage proves exclusive maintenance waits for an
  existing shared worker, and a worker that claimed before waiting performs zero
  sink writes after `pause_requested` is set.

## Worker and authority review

- `scan_once()` claims through `ProjectionDeliveryStore.claim_next()` only.
- After shared-lock acquisition it rechecks the active Partition, unexpired
  exact Delivery lease, exact-current claimed Attempt, and ordered cursor.
- It then invokes Task 5 `heartbeat()` as the authoritative second fence before
  exactly one executor apply.
- Executors must return a matching `ProjectionReceipt`.
- Outcomes use Task 5 token-guarded `mark_published()` / `record_failure()`;
  stale authority increments `ScanSummary.stale` and never writes the sink.
- Dead-letter Deliveries are not automatically claimed.
- Outbox facade signatures remain, but direct eligibility/status mutation code
  was removed and replaced with scoped Worker scans.

## Fault injection

Real PostgreSQL crash-window tests cover:

1. claim before adapter apply;
2. adapter semantic upsert before receipt;
3. receipt before DB publish;
4. DB publish before stale wake-up.

Termination is injected with a `BaseException` so the worker cannot turn a
simulated process death into a normal retry outcome. After forced lease expiry
where applicable, recovery finishes with one semantic upsert record and a
published Delivery. The post-publish stale wake-up claims nothing.

## PostgreSQL container

- Image: `postgres:16-alpine`
- Name: `p3a-task6-pg-fenced`
- Labels: `codex.task=p3a-task6`, `codex.owner=p3a-task6-impl`
- Database: `writer_task6_test` (the required `_test` suffix)
- Container was created only after listing existing containers. Cleanup verifies
  the exact container ID, name, image, and labels before removing only this
  Task 6 container.

## Tests

- Required Task 6 gate:
  `tests/unit/canonical/test_projection_worker.py`,
  `tests/integration/canonical/test_projection_advisory_locks.py`,
  `tests/integration/canonical/test_projection_crash_windows.py`, and
  `tests/unit/canonical/test_outbox_dispatcher.py`: `15 passed`.
- Fresh final gate including affected Barrier regression: `19 passed`.
- Ruff: all touched Task 6 files passed.
- Diff whitespace check: passed.

## Files

- Created `app/canonical/projection_locks.py`
- Created `app/canonical/projection_worker.py`
- Modified `app/canonical/outbox.py`
- Created `tests/unit/canonical/test_projection_worker.py`
- Created `tests/integration/canonical/test_projection_advisory_locks.py`
- Created `tests/integration/canonical/test_projection_crash_windows.py`
- Modified `tests/unit/canonical/test_outbox_dispatcher.py`

## Self-review / mutation check

- Wrong advisory key or missing release breaks the real lock race.
- Missing post-lock Partition/lease recheck causes a sink write in the pause race.
- Reusing Outbox status as eligibility breaks the stale-mirror facade test.
- Omitting Attempt authority breaks the facade Attempt outcome assertion.
- Catching process termination as a normal error breaks crash recovery state.
- Calling the adapter twice without semantic idempotency breaks the one-record
  crash-window assertion.
- Accepting mismatched/non-receipts is rejected before token-guarded publish.

## Concerns

- The broader canonical suite has pre-existing SQLite failures because migration
  `0003_p3a_projection_backfill.py` executes PostgreSQL `DO $$` SQL under SQLite.
  This is outside Task 6 files and the required PostgreSQL gate is green.
- Older Barrier/golden integration fixtures use `MagicMock()` projectors that
  return `None`, and some assume immediate retry without registry backoff. Task 6
  explicitly requires compatibility projectors to return `ProjectionReceipt`
  and preserves Task 5 retry authority, so those deprecated fixture assumptions
  were not restored through production compatibility hacks.
