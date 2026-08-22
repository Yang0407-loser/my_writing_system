# Task 5 Report: ordered projection leases and dead letters

## Status

Implemented strict ordered Delivery claiming, token-fenced outcomes, registry-driven retry/dead-letter transitions, transactional cursor and deprecated Outbox mirror writes, lag querying, and audited operator requeue.

## RED / GREEN evidence

- Initial RED: focused collection failed because `PermanentProjectionError`, `RetryableProjectionError`, `projection_delivery`, and `ProjectionRequeueAudit` did not exist.
- Mirror RED: the 20-session ownership test additionally proved that a successful claim left the deprecated Outbox mirror at `pending/0` instead of `processing/1`.
- Final focused GREEN: `21 passed in 2.97s` for the Task 5 unit, PostgreSQL integration, and schema suites.
- Ruff: `All checks passed!` for every changed Python file.
- Extra canonical-unit regression: `98 passed, 1 failed, 3 errors`; the four failures are existing SQLite schema tests attempting to execute PostgreSQL-only migration 0003 `DO $$` syntax. Task 5 focused tests and real PostgreSQL migrations are green.

## SQL and transaction decisions

- PostgreSQL claim is one atomic CTE: eligible rows join active partitions, apply scope filters and the required no-hole `NOT EXISTS`, lock `FOR UPDATE OF candidate SKIP LOCKED`, then update lease owner/token/expiry and increment attempts.
- Claim reads only `projection_deliveries`, `projection_partitions`, and immutable Envelope fields needed for filters. Deprecated Outbox status is never an eligibility input.
- Lease duration and retry limits/backoff come from the immutable projector registry. Backoff is exponential from `base_delay_seconds`, capped at `max_delay_seconds`.
- Claim, append-only Attempt creation/expired-attempt evidence, and Outbox mirror processing/attempt values commit together.
- Heartbeat, publish, and failure use `status='processing'` plus the current `lease_token`. Stale tokens affect zero rows.
- Publish changes Delivery, finalizes Attempt, advances the exact prior Partition cursor, and writes the Outbox mirror in one transaction. A cursor mismatch rolls the whole acknowledgment back.
- Retry/permanent/exhausted failure changes Delivery, finalizes Attempt, and writes the Outbox mirror in one transaction.
- Requeue accepts only dead-letter rows, requires nonblank operator/reason, keeps `attempt_count` and every claim Attempt unchanged, appends a requeue audit, and returns Delivery/mirror to pending in one transaction.

## Plan-discovered schema correction

The existing `ProjectionAttempt` unique key `(delivery_id, attempt_number)` makes a requeue pseudo-attempt corrupt or collide with claim attempt numbering. With task-owner approval, Task 5 adds minimal append-only `ProjectionRequeueAudit` evidence and additive migration `0004_p3a_requeue_audit`. It has no status, lease, or availability fields and is never scheduling authority.

## PostgreSQL contention proof and container lifecycle

- Created uniquely named `writer-p3a-task5-pg16-20260812` from `postgres:16-alpine`.
- Used only database `writer_task5_test` through `TEST_CANONICAL_DATABASE_URL`; the fixture enforced the `_test` suffix.
- Verified container ID `d86e6a342d052db2097fa0915ec9d3dc1dcdf57ae5e74aae9a78ee464ec7d941`, name, image, and readiness.
- Real PostgreSQL tests cover 20 simultaneous sessions, cross-projector concurrency, no-hole blocking, expired reclaim and stale fencing, transactional cursor advancement, unrelated-partition SKIP LOCKED behavior, audited requeue, and lag.
- Identity was rechecked immediately before stopping/removing only `writer-p3a-task5-pg16-20260812`. The database/container is not recoverable; it contained test-only data.

## Files

- `app/canonical/projection_delivery.py`
- `app/canonical/errors.py`
- `app/canonical/models.py`
- `migrations/versions/0004_p3a_projection_requeue_audit.py`
- `tests/unit/canonical/test_projection_delivery_state.py`
- `tests/integration/canonical/test_projection_delivery_claim.py`
- `tests/unit/canonical/test_projection_schema_v1.py`

## Self-review

- Scheduling authority remains PostgreSQL Delivery state; no P3A scheduling query reads the rollback mirror.
- Same tenant/project/projector order is fenced by the no-hole guard; independent partitions remain claimable.
- Every reclaim generates a fresh token and closes prior claimed Attempt evidence as lease-expired.
- Error class/message are bounded before persistence; no payload or credentials enter requeue audit.
- No worker loop, advisory maintenance lock, or rebuild behavior was added.

## Concerns

- The SQLite fallback is intentionally single-worker only and raises when explicitly asked to prove multi-worker safety; production contention evidence is PostgreSQL 16.
- `lag()` is an event-count aggregate from Canon head minus Partition cursor; richer age/count health is owned by the later projection-health task.
- Broad canonical SQLite migration tests are already incompatible with PostgreSQL-only migration 0003. This predates Task 5 and does not affect the mandatory real-PostgreSQL gate.

## Fix Round 1

### Accepted review findings and RED

- Missing Delivery gap: deleting position 1 while the cursor remained 0 allowed position 2 to claim. The new real-PostgreSQL test failed with an actual position-2 `DeliveryClaim`.
- Attempt CAS: forged `attempt_id` values allowed both publish and failure to return true and commit Delivery/mirror/cursor changes while the real Attempt remained `claimed`.
- Registry SQL/version: a registered ID containing `evil' OR 1=1 --` produced PostgreSQL syntax failure because projector IDs were interpolated into the lease CASE. An unknown version was also claimed, and heartbeat accepted a processing delivery after its version became unregistered.
- Stranding follow-up: an unknown projector ID/version initially remained pending forever. A strengthened RED required durable dead-letter without creating a claim Attempt.

### Fixes

- Claim retains the prior-row `NOT EXISTS` guard and now also requires `candidate.stream_position = partition.last_published_position + 1`.
- Registry ID, version, and lease seconds are supplied only through bound `VALUES` parameters. Eligibility requires exact registered delivery and partition `(projector_id, projector_version)` identity.
- Pending unregistered ID/version rows in active partitions are fenced from claim and durably dead-lettered with `UnknownProjectorVersionError`; the deprecated mirror is updated in the same transaction and no Attempt is invented.
- Publish/failure Attempt finalization is a CAS over `attempt_id + delivery_id + lease_token + outcome='claimed'`. A zero-row Attempt update rolls the complete outcome transaction back.
- Heartbeat resolves the processing Delivery's actual registered ID/version and rejects invalid registrations deterministically. Failure converts invalid registration into a permanent `UnknownProjectorVersionError` dead letter.
- PostgreSQL schema-head and backfill tests now expect `0004_p3a_requeue_audit`.

### Verification

- RED PostgreSQL Task 5 run: `6 failed, 10 passed`; every blocking review finding reproduced independently.
- Strengthened no-stranding RED: `2 failed` because unknown registrations remained pending.
- Final complete gate:
  `pytest tests/unit/canonical/test_projection_delivery_state.py tests/unit/canonical/test_projection_schema_v1.py tests/integration/canonical/test_projection_delivery_claim.py tests/integration/canonical/test_postgres_schema_v0.py tests/integration/canonical/test_projection_migration_v1.py -q`
  → `37 passed in 8.89s`.
- Ruff on every Fix Round 1 Python file → `All checks passed!`.

### Container lifecycle

- Used uniquely named `writer-p3a-task5-fix1-red-pg16-20260812`, image `postgres:16-alpine`, database `writer_task5_fix1_red_test`, mapped only on loopback.
- Verified container identity/readiness before testing. Identity was rechecked immediately before removing only this container after all tests.

### Files

- `app/canonical/projection_delivery.py`
- `tests/integration/canonical/test_projection_delivery_claim.py`
- `tests/integration/canonical/test_postgres_schema_v0.py`
- `tests/integration/canonical/test_projection_migration_v1.py`

### Remaining concerns

- The deferred `lag()` filter semantics finding remains intentionally outside Fix Round 1.

## Fix Round 2

### RED and root cause

- Real PostgreSQL tests mutated only Delivery `projector_version` or `projector_id`, leaving the active registered Partition unchanged. Both targets stayed pending because invalid discovery required exact Delivery/Partition identity.
- An empty Registry returned before invalid discovery, leaving all applicable Deliveries pending.
- RED result: `3 failed, 17 deselected`; every new case failed on expected `pending` instead of `dead_letter`.

### Deterministic partition semantics and fix

- Invalid mutable Delivery identity is mapped only through its immutable Envelope `projection_name` within the same tenant/project. This identifies the applicable original Partition without accepting a corrupted Delivery ID as another projector's identity.
- Invalid discovery requires that Envelope partition to remain `enrollment_status='active'` and `runtime_status='active'`. Explicit tests prove disabled and maintenance contexts remain pending rather than being incorrectly dead-lettered.
- Registry values remain bound. An empty Registry uses a typed empty-sentinel relation so invalid discovery still runs; applicable rows are dead-lettered and no claim/Attempt is created.
- Exact-next cursor and Attempt-CAS behavior are unchanged.

### Verification

- Focused Fix Round 2 GREEN: `3 passed, 17 deselected`.
- Complete Task 5 plus PostgreSQL schema/migration gate: `41 passed in 9.88s`.
- Ruff: `All checks passed!`.

### Container lifecycle

- Used uniquely named `writer-p3a-task5-fix2-red-pg16-20260812`, image `postgres:16-alpine`, database `writer_task5_fix2_red_test`, loopback port only.
- Verified exact identity and readiness before RED. Reverified immediately before stopping/removing only this container after final GREEN.

### Files and concerns

- Changed `app/canonical/projection_delivery.py` and `tests/integration/canonical/test_projection_delivery_claim.py`.
- Deferred `lag()` filter semantics remains untouched as directed.

## Fix Round 3

### RED and root cause

- A spoof-projector-scoped scan acted on a corrupted analytics Delivery because projector filters were applied to mutable `candidate.projector_id`.
- An analytics-scoped scan missed an analytics Envelope whose Delivery ID was changed to another registered projector ID; mutable Delivery identity could also select that other Partition during claim.
- Unscoped cleanup failed to dead-letter this registered-ID spoof before another valid partition claim.
- A Delivery barrier-kind mismatch remained claimable.
- Real PostgreSQL RED: `4 failed, 21 deselected`, with each failure reaching the intended routing/mismatch behavior after removing the conflicting same-position fixture row.

### Immutable routing fix

- `OutboxEvent.projection_name` is now the only projector routing identity for `ScanFilter.projector_id`, Partition selection, and Registry selection.
- `ScanFilter.barrier_kind` applies to immutable Envelope barrier kind.
- Claim joins the active same-scope Partition by Envelope projector and Registry by Envelope projector plus Partition version. It then requires Delivery projector ID, version, and barrier kind to exactly match Envelope/Partition authority.
- Cleanup uses the same Envelope/Partition/Registry authority and durably dead-letters any Delivery ID/version/barrier mismatch. A spoof-ID-scoped scan cannot act; only the original Envelope scope can clean it up.
- An invalid original-scope target and unrelated valid target remain partition-isolated: cleanup occurs first and an unrelated valid partition may still claim.
- Exact-next cursor, Attempt CAS, empty Registry handling, and bound Registry values are unchanged.

### Verification and lifecycle

- Focused Round 3 GREEN: `4 passed, 21 deselected`.
- Complete Task 5 plus PostgreSQL schema/migration gate: `45 passed in 10.57s`.
- Ruff: `All checks passed!`.
- Used `writer-p3a-task5-fix3-red-pg16-20260812`, `postgres:16-alpine`, database `writer_task5_fix3_red_test`, loopback only. Verified identity/readiness before testing and reverified before removing only that container.

### Files and concerns

- Changed `app/canonical/projection_delivery.py` and `tests/integration/canonical/test_projection_delivery_claim.py`.
- Deferred `lag()` filter semantics remains untouched.

## Fix Round 4

### RED and root cause

- Real PostgreSQL fixtures moved an analytics Delivery to another existing project, and to another existing tenant/project, where an active analytics Partition was at exact-next. Both corrupted rows were claimed from the spoof scope because tenant/project filters, Partition joins, and the no-hole guard still read mutable Delivery scope.
- A separate fixture changed both Delivery and immutable Envelope barrier kinds from the registered analytics `non_blocking` value to `critical`. The row was not classified because the bound Registry CTE carried only projector ID/version/lease and could not enforce barrier agreement.
- Focused RED: `3 failed, 25 deselected`; the two scope cases returned actual spoof-scope claims and the registry-barrier case remained pending.

### Immutable scope and Registry barrier fix

- Claim and cleanup now root their `FROM`, `ScanFilter` tenant/project/projector/barrier predicates, and active Partition joins in immutable Envelope tenant/project/projector identity.
- The ordered prior-row guard now determines partition and position from prior immutable Envelopes rather than mutable Delivery routing columns.
- Eligibility requires Delivery tenant, project, projector ID, projector version, barrier kind, and stream position to agree exactly with the Envelope/Partition/Registry authorities available for each field.
- The parameter-bound Registry relation now carries `ProjectorSpec.barrier_kind`; claim requires Envelope/Registry agreement and cleanup dead-letters Registry barrier mismatches without creating an Attempt.
- Original Envelope-scoped cleanup dead-letters corrupted Deliveries while an unrelated valid partition can still be claimed. Spoof-scoped scans neither claim nor clean the corrupted row.
- The single-worker SQLite fallback was aligned to Envelope filter/routing identity, without changing its explicit inability to prove multi-worker safety.

### Verification and lifecycle

- Focused Round 4 GREEN: `3 passed, 25 deselected`.
- Complete Task 5 plus PostgreSQL schema/migration gate: `48 passed in 10.88s`.
- Ruff on the changed Python files: `All checks passed!`.
- Used uniquely named `writer-p3a-task5-fix4-red-pg16-20260812`, image `postgres:16-alpine`, database `writer_task5_fix4_red_test`, loopback only. Verified exact container ID/image/readiness before RED; identity is rechecked immediately before removing only this test container.

### Files and concerns

- Changed `app/canonical/projection_delivery.py` and `tests/integration/canonical/test_projection_delivery_claim.py`.
- No broader design conflict was found: this completes the existing immutable-Envelope authority model rather than changing it.
- Deferred `lag()` filter semantics remains untouched as directed.

## Breaker remediation: exact-current Attempt authority

### RED and root cause

- A real PostgreSQL fixture first created the legitimate current claim/Attempt, then inserted another `claimed` Attempt for the same Delivery and lease token with a different attempt number.
- The processing authority query selected the caller-supplied Attempt by ID, Delivery, token, and outcome without requiring its attempt number to equal the Delivery attempt count. Heartbeat, publish, and failure therefore returned `True` for the noncurrent Attempt and committed durable changes.
- The expired scanner selected a claimed Attempt by Delivery/token/outcome only. With two matching rows it could choose the noncurrent Attempt instead of the exact current Attempt, so cleanup finalized the wrong evidence.
- Focused real-PostgreSQL RED: `4 failed, 36 deselected`; all three forged outcome paths returned `True`, and scanner cleanup left the legitimate current Attempt `claimed`. These were behavioral failures after valid setup, not fixture errors.

### Minimal fix and coverage

- `_lock_processing_identity()` now requires `ProjectionAttempt.attempt_number == ProjectionDelivery.attempt_count` in its locked Attempt query.
- `_ProcessingIdentity.agrees()` independently enforces the same equality as a defensive identity check.
- Expired scanner cleanup uses the same exact-current predicate when selecting claimed Attempt evidence.
- The PostgreSQL regressions prove the noncurrent Attempt has zero authority for heartbeat, publish, and failure, including no Delivery lease/outcome, current-Attempt, mirror, or cursor mutation. Each case then proves the legitimate exact-current Attempt still works.
- The scanner regression proves expired corrupted cleanup deterministically finalizes only the exact current Attempt, leaves the forged row untouched, avoids multiple-row ambiguity, and does not block an unrelated valid partition claim.

### GREEN and mutation proof

- Focused GREEN after the minimal fix: `4 passed, 36 deselected in 1.38s`.
- Controlled mutation removed the exact-current predicates from both the processing and scanner queries while retaining the defensive agreement check. The focused suite failed `4 failed, 36 deselected`: forged calls quarantined the Delivery instead of having zero authority, and scanner cleanup finalized the wrong Attempt.
- The predicates were restored and the focused suite returned to `4 passed, 36 deselected in 1.46s`.

### Verification, lifecycle, and self-review

- Complete Task 5 plus PostgreSQL schema/migration gate: `60 passed in 15.53s`.
- Ruff on `app/canonical/projection_delivery.py` and `tests/integration/canonical/test_projection_delivery_claim.py`: `All checks passed!`.
- `git diff --check`: passed.
- Used only `writer-p3a-task5-breaker-red-pg16-20260812`, exact container ID `ce0468c87f9e87214c34ad73fd9e3b0e41ef4168cad2adf2be5e1a6a0e07e03d`, image `postgres:16-alpine`, database `writer_task5_breaker_test`, loopback port `9105`. Identity/image/database were reverified immediately before stopping and permanently removing only this test container.
- Self-review found no widened scheduling authority or weakened token, immutable-envelope, Registry, CAS, quarantine, mirror, or cursor fences. No deferred `lag()` filters or Task 6 work were touched.

### Concerns

- None for this remediation scope. Deferred `lag()` filter semantics remains intentionally outside Task 5 breaker remediation.

## Breaker remediation follow-up: exact-current expired reclaim

### RED and root cause

- A real PostgreSQL fixture created legitimate Attempt `#1`, inserted forged noncurrent Attempt `#0` with the same Delivery and lease token, then performed a valid expired reclaim that created Attempt `#2` and also claimed an unrelated `task_preview` partition.
- `_expire_prior_attempt()` filtered only by Delivery, `outcome='claimed'`, and old token. Because the reclaiming Delivery row had already incremented `attempt_count` before this helper ran, both `#1` and forged `#0` were changed to `lease_expired`.
- Focused RED: `1 failed, 40 deselected`; reclaim and unrelated progress succeeded, `#1` and `#2` had the expected states, but forged `#0` was `lease_expired` instead of remaining `claimed`.

### Minimal fix, mutation, and verification

- The PostgreSQL claim path derives the previous exact-current number as the returned post-increment `attempt_count - 1` and passes it to `_expire_prior_attempt()`.
- Reclaim finalization now additionally requires `ProjectionAttempt.attempt_number == previous_attempt_number`, so only `#1` is finalized before `#2` is appended.
- Focused GREEN: `1 passed, 40 deselected in 0.68s`.
- Controlled mutation removed only the previous-attempt-number predicate; the focused test returned to the targeted `1 failed, 40 deselected`, with forged `#0` incorrectly `lease_expired`. Restoring the predicate returned `1 passed, 40 deselected in 0.66s`.
- Complete Task 5 plus PostgreSQL schema/migration gate: `61 passed in 15.22s`.
- Ruff on the two changed Python files: `All checks passed!`; `git diff --check`: passed.

### Lifecycle and self-review

- Used only `writer-p3a-task5-reclaim-red-pg16-20260812`, exact container ID `7e4cf55ad97b256fdf94e191db556c842fdea55b53ac5858b674446b035aa781`, image `postgres:16-alpine`, database `writer_task5_reclaim_test`, loopback port `6488`. Identity/image/database were reverified immediately before stopping and permanently removing only this container.
- Self-review confirmed the new predicate narrows only prior-Attempt finalization; claim scheduling, token fences, exact-current outcome authority, scanner cleanup, mirror/cursor semantics, deferred `lag()` filters, and Task 6 remain unchanged.

### Concerns

- None for this follow-up scope.
