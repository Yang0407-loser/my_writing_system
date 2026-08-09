# P3A Projection / Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a PostgreSQL-authoritative, lease-based, strictly ordered Projection runtime in which all seven P2 projections are idempotent, observable, dead-letterable, and deterministically rebuildable from Canon without relying on Celery for correctness.

**Architecture:** Keep each P2 `outbox_events` row as an immutable commit-time Projection Envelope and add exactly one mutable `projection_deliveries` row per Envelope. PostgreSQL owns Delivery, Lease, Retry, Dead-letter, Cursor, Rebuild and Reconciliation state; Celery only wakes the same bounded PostgreSQL scanner used by the independent recovery process. Maintenance Rebuild and new-Projector Bootstrap replay Canon through the same idempotent adapters, but persist only a durable run/checkpoint rather than a second per-event queue.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0, Alembic, PostgreSQL 17, psycopg 3, Pydantic 2, Celery 5.6, Redis 7, ChromaDB, pytest, Ruff, Docker Compose, PowerShell.

## Global Constraints

- Foundation baseline is `narrative-os-foundation-v1` / `dfd0edb`; P3A branch starts from that baseline.
- `outbox_events` keeps the P2 one-Envelope-per-Projector shape and preserves existing IDs and `CanonicalCommitResult.outbox_event_ids`.
- Envelope identity, Canon references, routing, event type and payload are immutable after commit; deprecated P2 status columns may remain only as non-authoritative rollback mirrors.
- `projection_deliveries` is the only incremental work unit and the only scheduling state read by new workers.
- PostgreSQL is the sole authority for Delivery, Lease, Retry, Dead-letter, Cursor, Rebuild, Projector enrollment and Reconciliation.
- Celery messages are optional wake-up hints. Loss, duplication, reordering or outage must not lose work or prevent scanner recovery.
- Same `(tenant_id, project_id, projector_id)` is strictly ordered; concurrency is only across different projects or Projectors.
- Execution is at-least-once. Every Projector must converge under duplicate execution; do not claim cross-system exactly-once.
- Critical Projector Dead-letter blocks Projection readiness, never Canon commitment. Non-blocking Dead-letter degrades health without blocking the critical Barrier.
- Maintenance Rebuild is Projection-scoped; Canon writes continue. Online generation/shadow cutover is not implemented or experimentally explored.
- Rebuild state is one durable `projection_rebuild_runs` record plus checkpoint, not per-event rebuild items.
- New Projectors never backfill historical Outbox Envelopes or Deliveries. They bootstrap from Canon, reconcile, then start fan-out only for Commit positions strictly greater than `activation_after_position`.
- `activation_after_position = H` is an exclusive threshold. Fan-out condition is `stream_position > activation_after_position`; do not model it as the first existing future Commit position.
- P3A Gate requires real PostgreSQL. SQLite can test pure contracts and state machines but cannot supply concurrency or advisory-lock evidence.
- Existing P0/P1/P2 Foundation tests, snapshot contracts, API compatibility, secret hygiene and internal-required semantics must remain green.
- `internal_required` remains internal dogfood only and does not mean external production ready.
- No new Kafka, workflow engine, shadow-generation namespace, dual-read pointer or same-stream unordered parallelism.

---

## 1. File and responsibility map

### Canonical core

- Modify `app/canonical/models.py`: P3A ORM schema, stream positions, Deliveries, Attempts, Partitions, Rebuild Runs, Reconciliation evidence and analytics projection rows.
- Create `migrations/versions/0002_p3a_projection_expand.py`: additive columns/tables/indexes with nullable stream fields.
- Create `migrations/versions/0003_p3a_projection_backfill.py`: deterministic Canon-chain position backfill, P2 Envelope→Delivery conversion, baseline enrollment and constraints.
- Create `app/canonical/projection_registry.py`: immutable Projector specs, versions, criticality and retry policy; no runtime work state.
- Modify `app/canonical/commit_service.py`: allocate project-local stream position and atomically create Envelope + Delivery for active enrollment rows.
- Modify `app/canonical/repositories.py`: initialize baseline enrollment, lock Head, query enrollment and expose Canon stream reads.
- Modify `app/canonical/snapshot.py`: preserve Foundation snapshot compatibility and reconstruct Delivery control state from imported Envelopes.

### Delivery runtime

- Create `app/canonical/projection_delivery.py`: strict ordered PostgreSQL claim CTE, heartbeat, completion, retry, Dead-letter, audited requeue and lag queries.
- Create `app/canonical/projection_locks.py`: deterministic shared/exclusive PostgreSQL advisory-lock context managers.
- Create `app/canonical/projection_worker.py`: bounded scan/process loop shared by inline dispatch, Celery wake-up and independent scanner.
- Modify `app/canonical/outbox.py`: compatibility facade over the new Delivery worker; no direct Outbox scheduling.
- Modify `app/canonical/projection_barrier.py`: Delivery/Partition/Cursor-based critical readiness.
- Modify `app/canonical/projection_ports.py`: immutable replayable message, receipt, normalized record and rebuildable adapter protocols.
- Create `app/canonical/projection_replay.py`: reconstruct Projection input directly from Canon for incremental execution and historical replay.

### Projection adapters

- Create `app/projections/__init__.py` and `app/projections/base.py`: adapter base types and normalized hashing helpers.
- Create `app/projections/legacy_world.py`: deterministic WorldState/EventGraph projection.
- Create `app/projections/handover.py`: deterministic handover history projection.
- Create `app/projections/chroma_story.py`: deterministic chunk upsert/list/clear.
- Create `app/projections/redis_stream.py`: deterministic Redis Stream IDs and normalized inspection.
- Create `app/projections/task_preview.py`: latest-Canon Redis preview upsert/inspect/clear.
- Create `app/projections/markdown_export.py`: atomic scoped Markdown replacement/inspect/clear.
- Create `app/projections/analytics.py`: unique PostgreSQL derived analytics rows.
- Create `app/projections/factory.py`: construct the seven production adapters from durable scope/message data; no Coordinator closure dependency.
- Modify `app/writing/legacy_subsection_projection.py`: compatibility wrapper over Canon message loading/adapters; remove Outbox existence as a rebuild prerequisite.
- Modify `app/world_state.py`, `app/blackboard.py`, `app/vector_store.py`, and `app/writing/subsection_handover_persistence.py`: deterministic upsert/list/clear primitives required by adapters.

### Rebuild, activation and operations

- Create `app/canonical/projection_manifest.py`: expected/actual normalized Manifest and diff.
- Create `app/canonical/projection_rebuild.py`: durable maintenance Rebuild and bootstrap state machines.
- Create `app/canonical/projection_health.py`: database-derived lag, Lease, Dead-letter, Rebuild and mismatch health snapshot.
- Create `app/canonical/projection_cli.py`: independent continuous scanner, bounded drain, rebuild, bootstrap, status and requeue commands.
- Create `app/projection_tasks.py`: best-effort Celery wake-up task calling `scan_once()` only.
- Modify `app/celery_app.py`, `app/config.py`, `.env.example`, `docker-compose.yml`: wake-up route, scanner settings and independent scanner service.
- Modify `app/writing/canonical_subsection_runtime.py` and `app/coordinator.py`: post-commit wake-up, inline critical drain through Delivery Lease, no Coordinator-owned correctness state.

### Tests, Gate and evidence

- Create focused unit tests under `tests/unit/canonical/` and `tests/unit/projections/`.
- Create real PostgreSQL tests under `tests/integration/canonical/` for migration, claim, advisory locks, crash windows, rebuild and bootstrap races.
- Create `scripts/p3a/run_projection_gate.py` and `scripts/p3a/verify_projection_gate.py`.
- Create `reports/p3a/p3a-gate-evidence.json`, `reports/p3a/p3a-gate-summary.md`, and `docs/runbooks/p3a-projection-operations.md` only from fresh passing evidence.

---

### Task 1: Freeze Projector contracts and P3A schema shape

**Files:**

- Create: `app/canonical/projection_registry.py`
- Modify: `app/canonical/projection_ports.py`
- Modify: `app/canonical/models.py`
- Create: `tests/unit/canonical/test_projection_registry.py`
- Create: `tests/unit/canonical/test_projection_schema_v1.py`

**Interfaces:**

- Produces: `RetryPolicy`, `ProjectorSpec`, `ProjectorRegistry`, `BASELINE_PROJECTOR_SPECS`.
- Produces: `ProjectionMessage`, `ProjectionReceipt`, `ProjectionRecord`, `ProjectionManifest`, `ProjectionAdapter`.
- Produces ORM models consumed by every later task.

- [ ] **Step 1: Write failing registry and schema tests**

```python
def test_baseline_registry_has_exact_p2_manifest():
    assert [(s.projector_id, s.barrier_kind) for s in BASELINE_PROJECTOR_SPECS] == [
        ("legacy_world_event", "critical"),
        ("handover_context", "critical"),
        ("chroma_story_chunks", "critical"),
        ("redis_stream", "non_blocking"),
        ("task_preview", "non_blocking"),
        ("markdown_export", "non_blocking"),
        ("analytics", "non_blocking"),
    ]

def test_delivery_is_one_to_one_with_envelope():
    constraints = {c.name for c in ProjectionDelivery.__table__.constraints}
    assert "uq_projection_delivery_envelope_projector" in constraints
```

Also assert every status/check constraint, scoped index and required Lease field described below exists in SQLAlchemy metadata.

- [ ] **Step 2: Run the tests and verify Red**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_registry.py tests\unit\canonical\test_projection_schema_v1.py -q
```

Expected: collection fails because P3A types/models do not exist.

- [ ] **Step 3: Add immutable registry and port contracts**

Implement these exact public shapes:

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_delay_seconds: int
    max_delay_seconds: int
    lease_seconds: int
    heartbeat_seconds: int

@dataclass(frozen=True)
class ProjectorSpec:
    projector_id: str
    version: str
    barrier_kind: Literal["critical", "non_blocking"]
    retry: RetryPolicy

class ProjectorRegistry:
    def __init__(self, specs: Iterable[ProjectorSpec]): ...
    def get(self, projector_id: str) -> ProjectorSpec: ...
    def all(self) -> tuple[ProjectorSpec, ...]: ...

DEFAULT_PROJECTOR_REGISTRY = ProjectorRegistry(BASELINE_PROJECTOR_SPECS)

def projection_event_id(projector_id: str, commit_id: str) -> str: ...

class ProjectionMessage(FrozenArtifact):
    projection_event_id: str
    outbox_event_id: str | None = None
    delivery_id: str | None = None
    tenant_id: str
    project_id: str
    commit_id: str
    revision_id: str
    state_version_id: str
    projector_id: str
    barrier_kind: Literal["critical", "non_blocking"]
    event_type: str
    stream_position: int = Field(ge=1)
    payload: dict[str, Any]

class ProjectionRecord(FrozenArtifact):
    record_id: str
    stream_position: int = Field(ge=1)
    commit_id: str
    revision_id: str
    payload: dict[str, Any]

class ProjectionReceipt(FrozenArtifact):
    projection_event_id: str
    projector_id: str
    projector_version: str
    stream_position: int
    record_count: int
    content_digest: str

class ProjectionManifest(FrozenArtifact):
    projector_id: str
    projector_version: str
    tenant_id: str
    project_id: str
    watermark_position: int
    record_count: int
    content_digest: str
    coverage_digest: str
    ledger_digest: str | None = None

@dataclass(frozen=True)
class ProjectionScope:
    tenant_id: str
    project_id: str

class RebuildStatus(FrozenArtifact):
    run_id: str
    run_kind: Literal["maintenance", "projector_bootstrap"]
    status: Literal[
        "requested", "pausing", "clearing", "rebuilding", "reconciling",
        "catching_up", "completed", "failed", "reconciliation_failed",
    ]
    checkpoint_position: int
    watermark_position: int
    activation_after_position: int | None = None

class ProjectionExecutor(Protocol):
    spec: ProjectorSpec
    def apply(self, message: ProjectionMessage) -> ProjectionReceipt: ...

class ProjectionAdapter(ProjectionExecutor, Protocol):
    def clear(self, scope: ProjectionScope) -> None: ...
    def expected_records(self, messages: Iterable[ProjectionMessage]) -> tuple[ProjectionRecord, ...]: ...
    def actual_records(self, scope: ProjectionScope) -> tuple[ProjectionRecord, ...]: ...
```

`projection_event_id` is `sha256(projector_id + ":" + commit_id)`-derived and stable whether input came from a Delivery or Canon replay. Never use an Outbox UUID as semantic projection identity.

Freeze baseline retry defaults in the Registry rather than environment-dependent ad hoc values:

```python
CRITICAL_RETRY = RetryPolicy(
    max_attempts=8, base_delay_seconds=2, max_delay_seconds=300,
    lease_seconds=120, heartbeat_seconds=30,
)
NON_BLOCKING_RETRY = RetryPolicy(
    max_attempts=5, base_delay_seconds=2, max_delay_seconds=300,
    lease_seconds=120, heartbeat_seconds=30,
)
```

Per-Projector overrides require a Registry version change and tests; heartbeat extends long-running work, so do not inflate Lease duration based on expected sink latency.

- [ ] **Step 4: Add ORM models with exact state ownership**

Add:

- `CanonicalProject.next_stream_position`.
- `CanonicalCommit.stream_position`.
- `OutboxEvent.stream_position`.
- `ProjectionDelivery`: pending/processing/published/dead_letter, Lease token/by/until, attempt data, receipt and error fields.
- `ProjectionAttempt`: one immutable attempt number/token with final outcome and operator/rebuild audit metadata.
- `ProjectionPartition`: enrollment status, runtime status, cursor, version, `activation_after_position`, active rebuild.
- `ProjectionRebuildRun`: maintenance/bootstrap kind, phase, Watermark/checkpoint/activation threshold, Lease and Manifest fields.
- `ProjectionReconciliation`: expected/actual digests and diff summary.
- `ProjectionAnalyticsEvent`: derived unique analytics row keyed by semantic projection event ID.

Use `BigInteger` for positions and explicit named `CheckConstraint`, `UniqueConstraint` and scoped indexes. Keep legacy Outbox state columns for rollback mirror only.

- [ ] **Step 5: Run focused tests and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_registry.py tests\unit\canonical\test_projection_schema_v1.py -q
git add app\canonical\models.py app\canonical\projection_ports.py app\canonical\projection_registry.py tests\unit\canonical\test_projection_registry.py tests\unit\canonical\test_projection_schema_v1.py
git commit -m "feat: define P3A projection contracts and schema"
```

Expected: all focused tests pass.

### Task 2: Add Alembic expand and deterministic P2 backfill

**Files:**

- Create: `migrations/versions/0002_p3a_projection_expand.py`
- Create: `migrations/versions/0003_p3a_projection_backfill.py`
- Modify: `tests/integration/canonical/test_postgres_schema_v0.py`
- Create: `tests/integration/canonical/test_projection_migration_v1.py`

**Interfaces:**

- Consumes ORM/table names from Task 1.
- Produces database head `0003_p3a_projection_backfill` and populated Delivery/Partition control state.

- [ ] **Step 1: Write a real PostgreSQL upgrade fixture at P2 head**

The test must downgrade an isolated `_test` database to `0001_canonical_schema_v0`, insert these rows with SQL, then upgrade to head:

```python
P2_ROWS = [
    ("legacy_world_event", "published", 1, None),
    ("handover_context", "failed", 2, "redis unavailable"),
    ("chroma_story_chunks", "pending", 0, None),
]
```

Create two linear Canon commits through their State Version parent chain. Assert after upgrade:

- positions are 1 and 2 in chain order;
- Project Head counter is 2;
- every existing Envelope has exactly one Delivery;
- no additional Outbox Envelope exists;
- published maps to published with migration receipt;
- failed maps to pending with attempts/error preserved;
- processing maps to pending;
- baseline Partitions are active with `activation_after_position=0`;
- Cursor stops before the first non-published position and never crosses a hole.

- [ ] **Step 2: Run the migration test and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\integration\canonical\test_projection_migration_v1.py -q
```

Expected: FAIL because Alembic head is still `0001_canonical_schema_v0`.

- [ ] **Step 3: Implement additive expand migration**

`0002` must only add nullable position columns and create new tables/indexes. It must not rename, delete or normalize `outbox_events`, and must not create history for a Projector that lacks an Envelope.

- [ ] **Step 4: Implement deterministic backfill migration**

Use the Canon State Version parent chain, not timestamp order:

```sql
WITH RECURSIVE state_chain AS (
  SELECT project_id, id, commit_id, 0::bigint AS position
  FROM canonical_state_versions
  WHERE origin = 'genesis'
  UNION ALL
  SELECT child.project_id, child.id, child.commit_id, parent.position + 1
  FROM state_chain parent
  JOIN canonical_state_versions child
    ON child.project_id = parent.project_id
   AND child.parent_state_version_id = parent.id
)
UPDATE canonical_commits AS commit
SET stream_position = chain.position
FROM state_chain AS chain
WHERE chain.commit_id = commit.id;
```

Then copy Commit position to existing Envelopes, create one Delivery per existing Envelope, compute the continuous published prefix per Partition, set `NOT NULL`/unique constraints, and update each project counter. Abort migration if a committed non-genesis State cannot be placed on one linear project chain.

- [ ] **Step 5: Verify upgrade, downgrade-on-empty, and schema head**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\integration\canonical\test_projection_migration_v1.py tests\integration\canonical\test_postgres_schema_v0.py -q
```

Downgrade coverage is only for an empty/test schema. Operational rollback keeps the expand schema and stops P3A workers; it must not discard Delivery/Attempt evidence.

- [ ] **Step 6: Commit**

```powershell
git add migrations\versions\0002_p3a_projection_expand.py migrations\versions\0003_p3a_projection_backfill.py tests\integration\canonical\test_projection_migration_v1.py tests\integration\canonical\test_postgres_schema_v0.py
git commit -m "feat: migrate P2 outbox state into P3A deliveries"
```

### Task 3: Allocate Canon stream positions and atomically fan out Deliveries

**Files:**

- Modify: `app/canonical/repositories.py`
- Modify: `app/canonical/commit_service.py`
- Modify: `app/canonical/snapshot.py`
- Modify: `tests/unit/canonical/test_commit_service.py`
- Modify: `tests/unit/canonical/test_repositories.py`
- Modify: `tests/integration/canonical/test_commit_concurrency.py`
- Modify: `tests/integration/canonical/test_snapshot_restore.py`

**Interfaces:**

- Produces: `CanonicalRepository.next_stream_position(project)` and enrollment queries.
- Changes: `CanonicalCommitService(..., projector_registry=DEFAULT_PROJECTOR_REGISTRY)`.
- Produces atomically aligned Canon Commit, Envelope and Delivery rows.

- [ ] **Step 1: Write failing atomic fan-out tests**

```python
def test_commit_creates_one_delivery_per_existing_envelope(canonical_session):
    result = service.commit(prepared, "commit-1")
    envelopes = scoped_envelopes(result.commit_id)
    deliveries = scoped_deliveries(result.commit_id)
    assert len(envelopes) == len(deliveries) == 7
    assert {e.id for e in envelopes} == {d.outbox_event_id for d in deliveries}
    assert {e.stream_position for e in envelopes} == {1}

def test_disabled_registered_projector_gets_no_history_or_new_envelope_until_activation():
    assert count_envelopes("search_index") == 0
```

Also inject failures after stream allocation, Envelope insert and Delivery insert; assert the project counter, Canon tables, Envelope and Delivery all roll back.

- [ ] **Step 2: Run focused tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_commit_service.py tests\integration\canonical\test_commit_concurrency.py -q
```

Expected: FAIL because current commit creates mutable Outbox rows without Deliveries or positions.

- [ ] **Step 3: Initialize baseline enrollment on project creation**

`CanonicalRepository.create_project()` inserts seven active `ProjectionPartition` rows with cursor 0 and `activation_after_position=0`. A registered-but-disabled Projector gets a disabled row only through explicit bootstrap setup.

- [ ] **Step 4: Allocate position and fan out inside the existing Canon transaction**

After locking `CanonicalProject`, increment its project-local counter and assign the returned value to Canon Commit, all active Projector Envelopes and their one-to-one Deliveries. Fan-out predicate is exact:

```python
spec.projector_id in registry
and partition.enrollment_status == "active"
and stream_position > partition.activation_after_position
```

Create Envelope and Delivery before moving Heads and completing idempotency. Add failure hook stages `after_stream_position` and `after_projection_deliveries`.

- [ ] **Step 5: Preserve snapshot behavior**

Keep Canon snapshot schema backward-readable. Export Envelopes plus Delivery/Partition state for new snapshots; importing an old v0 snapshot reconstructs one Delivery only for each imported Envelope and derives continuous cursors. Never synthesize missing historical Envelopes.

- [ ] **Step 6: Run tests and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_commit_service.py tests\unit\canonical\test_repositories.py tests\integration\canonical\test_commit_concurrency.py tests\integration\canonical\test_snapshot_restore.py -q
git add app\canonical\repositories.py app\canonical\commit_service.py app\canonical\snapshot.py tests\unit\canonical\test_commit_service.py tests\unit\canonical\test_repositories.py tests\integration\canonical\test_commit_concurrency.py tests\integration\canonical\test_snapshot_restore.py
git commit -m "feat: create projection deliveries in canonical commits"
```

### Task 4: Reconstruct stable Projection messages directly from Canon

**Files:**

- Create: `app/canonical/projection_replay.py`
- Modify: `app/writing/legacy_subsection_projection.py`
- Modify: `tests/unit/test_legacy_subsection_projection.py`
- Create: `tests/unit/canonical/test_projection_replay.py`

**Interfaces:**

- Produces: `CanonicalProjectionReplay.iter_messages(scope, projector_id, after_position, through_position)`.
- Produces: `CanonicalProjectionReplay.message_for_delivery(delivery_id)`.
- Produces: `CanonicalProjectionReplay.materialize_document_at(scope, stream_position)` for as-of Markdown rendering.
- Removes historical Outbox existence as a Rebuild prerequisite.

- [ ] **Step 1: Write failing parity and no-Envelope tests**

```python
def test_delivery_and_canon_replay_have_same_semantic_identity():
    incremental = replay.message_for_delivery(delivery.id)
    rebuilt = tuple(replay.iter_messages(scope, "chroma_story_chunks", 0, 1))[0]
    assert incremental.projection_event_id == rebuilt.projection_event_id
    assert incremental.commit_id == rebuilt.commit_id
    assert incremental.payload == rebuilt.payload

def test_bootstrap_replay_does_not_require_historical_outbox():
    assert no_envelope("search_index", old_commit.id)
    assert tuple(replay.iter_messages(scope, "search_index", 0, old_commit.stream_position))
```

- [ ] **Step 2: Run tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_replay.py tests\unit\test_legacy_subsection_projection.py -q
```

Expected: FAIL because `_load_committed()` currently requires an Outbox UUID.

- [ ] **Step 3: Implement Canon replay reader**

Join committed `CanonicalCommit`, accepted `DocumentRevision`, `CanonicalStateVersion` and ordered `EventLedger`. Recompute revision/state hashes before yielding. Build the same deterministic `projection_event_id(projector_id, commit_id)` for both paths; set Outbox/Delivery IDs only for incremental messages.

`materialize_document_at()` selects, for every subsection, the newest accepted revision whose Commit position is less than or equal to the requested position. Never build a historical Markdown/Manifest from today's subsection Heads.

- [ ] **Step 4: Refactor the compatibility projector**

Move `LegacyProjectionEnvelope` construction to the Canon replay reader. `LegacySubsectionProjection` may keep its existing `project(message)` facade, but it must return a deterministic `ProjectionReceipt` and cannot query Outbox as proof that Canon exists. Scope, committed status and recomputable Canon hashes remain mandatory.

- [ ] **Step 5: Run tests and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_replay.py tests\unit\test_legacy_subsection_projection.py -q
git add app\canonical\projection_replay.py app\writing\legacy_subsection_projection.py tests\unit\canonical\test_projection_replay.py tests\unit\test_legacy_subsection_projection.py
git commit -m "refactor: build projection messages from Canon"
```

### Task 5: Implement strict ordered Lease, Retry, Dead-letter and audited requeue

**Files:**

- Create: `app/canonical/projection_delivery.py`
- Modify: `app/canonical/errors.py`
- Create: `tests/unit/canonical/test_projection_delivery_state.py`
- Create: `tests/integration/canonical/test_projection_delivery_claim.py`

**Interfaces:**

- Produces: `DeliveryClaim`, `ScanFilter`, `ProjectionDeliveryStore.claim_next()`, `heartbeat()`, `mark_published()`, `record_failure()`, `requeue_dead_letter()`, `lag()`.

Use these exact immutable request/result shapes:

```python
@dataclass(frozen=True)
class ScanFilter:
    tenant_id: str | None = None
    project_id: str | None = None
    projector_id: str | None = None
    commit_id: str | None = None
    barrier_kind: str | None = None
    limit: int = 100

@dataclass(frozen=True)
class DeliveryClaim:
    delivery_id: str
    outbox_event_id: str
    attempt_id: str
    lease_token: str
    leased_by: str
    leased_until: datetime
    tenant_id: str
    project_id: str
    projector_id: str
    stream_position: int
```

Error classification is explicit:

```python
class RetryableProjectionError(ProjectionError): ...
class PermanentProjectionError(ProjectionError): ...
class ProjectionConflictError(PermanentProjectionError): ...
```

Connection, timeout and rate-limit failures are retryable. Invalid Canon payload, unknown Projector version and deterministic sink conflicts are permanent. An unclassified exception is retryable until the spec's `max_attempts`, then Dead-lettered with its class/message recorded.

- [ ] **Step 1: Write pure state tests**

Assert retry classification produces:

```python
retryable -> status="pending", available_at=now+backoff
permanent -> status="dead_letter"
attempt_count >= max_attempts -> status="dead_letter"
```

Assert requeue requires non-empty `operator_id` and `reason`, retains previous Attempts, creates an audited requeue record and returns to pending.

- [ ] **Step 2: Write real PostgreSQL contention tests**

Use 20 concurrent sessions and assert:

- one Delivery has one current owner/token;
- different Projector/project partitions can be claimed concurrently;
- position N+1 cannot be claimed while N is pending, processing or dead-letter;
- expired N can be reclaimed with a new token;
- old token cannot heartbeat, publish or fail after reclaim;
- successful N advances Cursor transactionally and exposes N+1;
- `FOR UPDATE SKIP LOCKED` does not make workers wait on unrelated partitions.

- [ ] **Step 3: Run tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_delivery_state.py tests\integration\canonical\test_projection_delivery_claim.py -q
```

- [ ] **Step 4: Implement the PostgreSQL claim CTE**

The eligibility predicate must include a no-hole guard:

```sql
NOT EXISTS (
  SELECT 1 FROM projection_deliveries prior
  WHERE prior.tenant_id = candidate.tenant_id
    AND prior.project_id = candidate.project_id
    AND prior.projector_id = candidate.projector_id
    AND prior.stream_position < candidate.stream_position
    AND prior.status <> 'published'
)
```

Select pending/available or expired processing rows, lock `FOR UPDATE OF candidate SKIP LOCKED`, then update status, token, owner, expiry and attempt count in the same transaction. SQLite fallback may support unit tests but must raise if asked to prove multi-worker safety.

- [ ] **Step 5: Implement token-guarded outcomes and mirror writes**

Completion/failure updates include `WHERE id=:id AND status='processing' AND lease_token=:token`. Update the deprecated Outbox mirror in the same PostgreSQL transaction, but no P3A query may use that mirror for scheduling.

- [ ] **Step 6: Run tests and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_delivery_state.py tests\integration\canonical\test_projection_delivery_claim.py -q
git add app\canonical\projection_delivery.py app\canonical\errors.py tests\unit\canonical\test_projection_delivery_state.py tests\integration\canonical\test_projection_delivery_claim.py
git commit -m "feat: add ordered projection leases and dead letters"
```

### Task 6: Add maintenance fencing and the shared Projection Worker

**Files:**

- Create: `app/canonical/projection_locks.py`
- Create: `app/canonical/projection_worker.py`
- Modify: `app/canonical/outbox.py`
- Create: `tests/unit/canonical/test_projection_worker.py`
- Create: `tests/integration/canonical/test_projection_advisory_locks.py`
- Create: `tests/integration/canonical/test_projection_crash_windows.py`

**Interfaces:**

- Produces: `ProjectionMaintenanceLocks.shared(scope)` and `.exclusive(scope)`.
- Produces: `ProjectionWorker.scan_once(filter: ScanFilter) -> ScanSummary`.
- Keeps: `OutboxDispatcher.dispatch_critical/non_blocking/pending` as a temporary compatibility facade.

```python
@dataclass(frozen=True)
class ScanSummary:
    claimed: int = 0
    published: int = 0
    retried: int = 0
    dead_lettered: int = 0
    stale: int = 0
```

- [ ] **Step 1: Write advisory-lock race tests**

On PostgreSQL, hold a shared lock in Worker A and prove Rebuild B cannot acquire exclusive lock until A exits. Then set `pause_requested`, start a new Worker C, and assert C rechecks status after shared-lock acquisition and performs zero sink writes.

- [ ] **Step 2: Write crash-window tests**

Inject termination/failure at:

1. claim before adapter apply;
2. adapter apply before receipt;
3. receipt before DB publish;
4. DB publish before stale Celery wake-up.

The adapter spy uses semantic upsert keys. After Lease expiry and recovery, each test must end with one semantic record and a published Delivery.

- [ ] **Step 3: Run tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_worker.py tests\integration\canonical\test_projection_advisory_locks.py tests\integration\canonical\test_projection_crash_windows.py -q
```

- [ ] **Step 4: Implement deterministic advisory keys**

Derive two signed int32 keys from SHA-256 of `tenant_id/project_id/projector_id`. Use session-level `pg_advisory_lock_shared` for adapter writes and exclusive `pg_advisory_lock` for maintenance. Always release in `finally`; PostgreSQL connection loss also releases locks.

- [ ] **Step 5: Implement Worker process loop**

`scan_once()` repeatedly claims up to the requested limit, reconstructs Canon message, acquires shared maintenance lock, rechecks Partition/Lease, calls exactly one adapter, and records token-guarded outcome. It never trusts a Celery event ID and never processes Dead-letter automatically.

The Worker constructor accepts `Mapping[str, ProjectionExecutor]`. Task 4's compatibility projector must return `ProjectionReceipt`; Tasks 8–9 later replace those apply-only executors with full rebuildable `ProjectionAdapter` implementations.

- [ ] **Step 6: Replace direct Outbox dispatch internals**

Keep the P2 facade signatures so `CanonicalSubsectionRuntime` remains behaviorally stable, but each method scopes and invokes `ProjectionWorker.scan_once()` over Deliveries. Delete direct queries that treat `OutboxEvent.status` as eligibility.

- [ ] **Step 7: Run tests and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_worker.py tests\integration\canonical\test_projection_advisory_locks.py tests\integration\canonical\test_projection_crash_windows.py tests\unit\canonical\test_outbox_dispatcher.py -q
git add app\canonical\projection_locks.py app\canonical\projection_worker.py app\canonical\outbox.py tests\unit\canonical\test_projection_worker.py tests\integration\canonical\test_projection_advisory_locks.py tests\integration\canonical\test_projection_crash_windows.py tests\unit\canonical\test_outbox_dispatcher.py
git commit -m "feat: execute projections through fenced lease workers"
```

### Task 7: Move Barrier and canonical runtime onto Delivery/Cursor truth

**Files:**

- Modify: `app/canonical/projection_barrier.py`
- Modify: `app/writing/canonical_subsection_runtime.py`
- Modify: `app/coordinator.py`
- Modify: `tests/unit/canonical/test_projection_barrier.py`
- Modify: `tests/unit/test_canonical_subsection_runtime.py`
- Modify: `tests/integration/canonical/test_golden_projection_failures.py`

**Interfaces:**

- Keeps: `ProjectionBarrier.ensure_ready(commit_id) -> failed|pending|ready`.
- Adds health reasons internally without changing the existing public response contract.

- [ ] **Step 1: Write failing Barrier truth tests**

Assert:

- changing only deprecated `outbox_events.status` cannot make Barrier ready;
- three critical Deliveries published + active Partitions + covering Cursors makes ready;
- critical Dead-letter returns failed/degraded while Canon remains committed;
- maintenance/catching_up Partition returns pending even if historical Delivery says published;
- non-blocking Dead-letter does not change critical ready.

- [ ] **Step 2: Run tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_barrier.py tests\unit\test_canonical_subsection_runtime.py -q
```

- [ ] **Step 3: Implement Delivery/Cursor Barrier**

Join Commit position to its critical Deliveries and Partitions. Ready requires exactly the active critical manifest, published state, active runtime, matching Projector version and Cursor coverage. Missing rows fail closed.

- [ ] **Step 4: Preserve synchronous critical read-after-write through the Lease path**

After commit, runtime invokes a bounded inline critical scan through `ProjectionWorker`, checks Barrier, and only then allows the next subsection. Non-blocking work may be wake-up/scanner driven. No direct Projector call is allowed.

- [ ] **Step 5: Run tests and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_barrier.py tests\unit\test_canonical_subsection_runtime.py tests\integration\canonical\test_golden_projection_failures.py -q
git add app\canonical\projection_barrier.py app\writing\canonical_subsection_runtime.py app\coordinator.py tests\unit\canonical\test_projection_barrier.py tests\unit\test_canonical_subsection_runtime.py tests\integration\canonical\test_golden_projection_failures.py
git commit -m "feat: gate canonical reads on projection deliveries"
```

### Task 8: Make critical World, Handover and Chroma adapters rebuildable

**Files:**

- Create: `app/projections/__init__.py`
- Create: `app/projections/base.py`
- Create: `app/projections/legacy_world.py`
- Create: `app/projections/handover.py`
- Create: `app/projections/chroma_story.py`
- Modify: `app/world_state.py`
- Modify: `app/vector_store.py`
- Modify: `app/writing/subsection_handover_persistence.py`
- Create: `tests/unit/projections/test_critical_projection_adapters.py`
- Create: `tests/integration/canonical/test_critical_projection_rebuild.py`

**Interfaces:**

- Produces three complete `ProjectionAdapter` implementations.
- Adds deterministic sink primitives: World fact upsert, scoped history enumerate/clear, Chroma scoped list/delete.

- [ ] **Step 1: Write duplicate-apply and delete/replay tests**

For each adapter:

```python
receipt1 = adapter.apply(message)
receipt2 = adapter.apply(message)
assert receipt1.content_digest == receipt2.content_digest
before = adapter.actual_records(scope)
adapter.clear(scope)
assert adapter.actual_records(scope) == ()
adapter.apply(message)
assert adapter.actual_records(scope) == before
```

World tests must expose the current random-UUID duplicate and require a deterministic fact key derived from projection event + fact ordinal + normalized text. Handover uses its existing content-derived record ID. Chroma retains deterministic chunk IDs and must enumerate/delete by canonical project/task scope.

- [ ] **Step 2: Run tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\projections\test_critical_projection_adapters.py -q
```

- [ ] **Step 3: Implement normalized adapter base**

`ProjectionRecord` contains `record_id`, `stream_position`, `commit_id`, and canonicalized payload. Sort by `(stream_position, record_id)` before hashing with `sha256_json`.

- [ ] **Step 4: Implement deterministic World and Handover primitives**

Add `WorldStateManager.upsert_fact(fact_id=..., ...)` without changing legacy `add_fact()`. Adapter calls only deterministic upsert. Add recorder methods to list and clear only records belonging to the target Canon project/task scope; never delete unrelated Redis keys.

- [ ] **Step 5: Implement Chroma enumerate and clear**

Add VectorStore methods that filter canonical metadata by `project_id`, return normalized IDs/content hashes, and delete only those IDs. Include `project_id` and `stream_position` in new chunk metadata.

- [ ] **Step 6: Run unit and real Chroma restart tests**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\projections\test_critical_projection_adapters.py tests\integration\canonical\test_critical_projection_rebuild.py -q
```

The integration test reopens the same Chroma persistent path between apply and replay.

- [ ] **Step 7: Commit**

```powershell
git add app\projections\__init__.py app\projections\base.py app\projections\legacy_world.py app\projections\handover.py app\projections\chroma_story.py app\world_state.py app\vector_store.py app\writing\subsection_handover_persistence.py tests\unit\projections\test_critical_projection_adapters.py tests\integration\canonical\test_critical_projection_rebuild.py
git commit -m "feat: make critical projections idempotent and inspectable"
```

### Task 9: Replace non-blocking placeholders with real rebuildable adapters

**Files:**

- Create: `app/projections/redis_stream.py`
- Create: `app/projections/task_preview.py`
- Create: `app/projections/markdown_export.py`
- Create: `app/projections/analytics.py`
- Create: `app/projections/factory.py`
- Modify: `app/blackboard.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Create: `tests/unit/projections/test_nonblocking_projection_adapters.py`
- Create: `tests/integration/canonical/test_nonblocking_projection_rebuild.py`

**Interfaces:**

- Produces four complete non-blocking adapters and `build_projection_adapters(session_factory) -> Mapping[str, ProjectionAdapter]`.
- Adds `PROJECTION_MARKDOWN_ROOT` as a scoped derived-output root.

- [ ] **Step 1: Write tests that reject the current shared-reference placeholder**

Require distinct normalized outputs for `redis_stream`, `task_preview`, `markdown_export` and `analytics`. Duplicate apply, clear, replay and actual-record enumeration must converge independently for each adapter.

- [ ] **Step 2: Run tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\projections\test_nonblocking_projection_adapters.py -q
```

- [ ] **Step 3: Add deterministic Redis Stream writes**

Add `Blackboard.xadd_canonical_event(tenant_id, project_id, stream_position, event)`. Use a Redis key derived from SHA-256 of tenant/project scope and Redis ID `f"{stream_position}-0"`; if the ID already exists, compare normalized payload and return it only when identical, otherwise raise a permanent projection conflict. Add matching scoped stream list/clear methods for reconciliation and rebuild. A legacy task stream notification may reference the canonical stream key, but it is not the Projection record or scheduling authority.

- [ ] **Step 4: Add latest-preview and Markdown replace semantics**

Task Preview writes one versioned Redis record under a tenant/project-scoped key and rejects an attempt to overwrite a newer position. Markdown calls `materialize_document_at(message.stream_position)`, then writes that as-of Canon document to `<PROJECTION_MARKDOWN_ROOT>/<tenant-hash>/<project-hash>.md` using a sibling temporary file plus `os.replace`; validate the resolved path remains inside the configured root before delete/replace.

- [ ] **Step 5: Add derived analytics rows**

Use `ProjectionAnalyticsEvent` with unique `projection_event_id`; upsert normalized commit/revision/count/hash metadata. This table is derived and clearable by scope and is never read by Canon Commit logic.

- [ ] **Step 6: Build the standalone adapter factory**

The factory constructs dependencies from message scope/task metadata (`Blackboard`, `EventGraph`, `WorldStateManager`, `VectorStore`, handover recorder, Markdown root and SQL session). Coordinator closures may remain only as legacy-mode compatibility; P3A scanner must not depend on a live Coordinator object.

- [ ] **Step 7: Run tests and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\projections\test_nonblocking_projection_adapters.py tests\integration\canonical\test_nonblocking_projection_rebuild.py -q
git add app\projections\redis_stream.py app\projections\task_preview.py app\projections\markdown_export.py app\projections\analytics.py app\projections\factory.py app\blackboard.py app\config.py .env.example tests\unit\projections\test_nonblocking_projection_adapters.py tests\integration\canonical\test_nonblocking_projection_rebuild.py
git commit -m "feat: add rebuildable nonblocking projections"
```

### Task 10: Implement deterministic Manifest and fail-closed Reconciliation

**Files:**

- Create: `app/canonical/projection_manifest.py`
- Create: `tests/unit/canonical/test_projection_manifest.py`
- Create: `tests/integration/canonical/test_projection_reconciliation.py`

**Interfaces:**

- Produces: `build_manifest()`, `reconcile_projection()`, `ReconciliationResult`.
- Consumes all seven adapters from Tasks 8–9.

```python
@dataclass(frozen=True)
class ReconciliationResult:
    status: Literal["matched", "mismatch"]
    expected: ProjectionManifest
    actual: ProjectionManifest
    missing_ids: tuple[str, ...]
    extra_ids: tuple[str, ...]
    changed_ids: tuple[str, ...]
```

- [ ] **Step 1: Write canonicalization tests**

```python
def test_manifest_is_order_and_serialization_stable():
    assert build_manifest(scope, spec, watermark, records_a) == build_manifest(
        scope, spec, watermark, reversed(records_a)
    )

def test_count_match_cannot_hide_content_mismatch():
    result = reconcile_projection(expected, actual_same_count_different_hash)
    assert result.status == "mismatch"
```

Cover empty projections, duplicate IDs, missing records, extra records, revision/commit coverage and ledger digest.

- [ ] **Step 2: Run tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_manifest.py tests\integration\canonical\test_projection_reconciliation.py -q
```

- [ ] **Step 3: Implement normalized Manifest**

Manifest fields are exact: projector ID/version, scope, Watermark, record count, content digest, commit/revision coverage digest and optional ledger digest. Reject duplicate normalized record IDs rather than silently deduplicating them.

- [ ] **Step 4: Persist evidence without changing Canon**

Write a `ProjectionReconciliation` row for every check. A mismatch returns structured counts and bounded ID/hash samples, never full draft text or secrets. It cannot mutate Canon, Cursor or Delivery state.

- [ ] **Step 5: Run tests and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_manifest.py tests\integration\canonical\test_projection_reconciliation.py -q
git add app\canonical\projection_manifest.py tests\unit\canonical\test_projection_manifest.py tests\integration\canonical\test_projection_reconciliation.py
git commit -m "feat: reconcile projections with canonical manifests"
```

### Task 11: Implement resumable projection-scoped Maintenance Rebuild

**Files:**

- Create: `app/canonical/projection_rebuild.py`
- Create: `tests/unit/canonical/test_projection_rebuild_state.py`
- Create: `tests/integration/canonical/test_projection_rebuild.py`
- Create: `tests/integration/canonical/test_projection_rebuild_crash.py`

**Interfaces:**

- Produces: `ProjectionRebuildService.start_maintenance()`, `resume()`, `status()`.
- Consumes exclusive maintenance lock, Canon replay, adapters and Manifest service.

```python
class ProjectionRebuildService:
    def start_maintenance(
        self, scope: ProjectionScope, projector_id: str, *, operator_id: str, reason: str
    ) -> str: ...
    def resume(self, run_id: str, *, worker_id: str) -> RebuildStatus: ...
    def status(self, run_id: str) -> RebuildStatus: ...
```

- [ ] **Step 1: Write the exact state-machine test**

```python
assert transitions == [
    ("requested", "pausing"),
    ("pausing", "clearing"),
    ("clearing", "rebuilding"),
    ("rebuilding", "reconciling"),
    ("reconciling", "catching_up"),
    ("catching_up", "completed"),
]
```

Invalid skips fail closed. Rebuild Run Lease token guards phase/checkpoint updates exactly like Delivery Lease.

- [ ] **Step 2: Write real end-to-end Rebuild tests**

For each of seven adapters:

1. commit multiple Canon revisions;
2. record Watermark W while Canon continues to W+2;
3. pause only target Partition;
4. clear target Projection;
5. replay Canon `<=W` in stable batches;
6. reconcile expected/actual;
7. mark Deliveries `<=W` superseded/published and Cursor W in one transaction;
8. resume scanner and reach W+2/current with lag zero.

- [ ] **Step 3: Add crash injections**

Crash after pause, clear, external batch apply, before checkpoint, after reconciliation and before unpause. Resume with a new Worker and prove the same final Manifest. Inject one missing, extra and corrupted record and require `reconciliation_failed`, maintenance retained and Cursor unchanged.

- [ ] **Step 4: Run tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_rebuild_state.py tests\integration\canonical\test_projection_rebuild.py tests\integration\canonical\test_projection_rebuild_crash.py -q
```

- [ ] **Step 5: Implement durable run + checkpoint, not rebuild items**

Each batch replays Canon directly and persists only `checkpoint_position`, processed count and Run Lease. Pin `projector_version`; refuse resume under another version. On successful reconciliation, supersede Delivery rows through W with Attempt audit, advance Cursor, activate Partition and let normal scanner process positions greater than W.

- [ ] **Step 6: Run tests and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_rebuild_state.py tests\integration\canonical\test_projection_rebuild.py tests\integration\canonical\test_projection_rebuild_crash.py -q
git add app\canonical\projection_rebuild.py tests\unit\canonical\test_projection_rebuild_state.py tests\integration\canonical\test_projection_rebuild.py tests\integration\canonical\test_projection_rebuild_crash.py
git commit -m "feat: rebuild projections from Canon with checkpoints"
```

### Task 12: Implement new-Projector Bootstrap and atomic activation threshold

**Files:**

- Modify: `app/canonical/projection_rebuild.py`
- Modify: `app/canonical/commit_service.py`
- Create: `tests/integration/canonical/test_projector_bootstrap.py`
- Create: `tests/integration/canonical/test_projector_activation_race.py`

**Interfaces:**

- Produces: `ProjectionRebuildService.start_bootstrap(scope, projector_id)`.
- Freezes: `activation_after_position=H`; fan-out only where Commit position `>H`.

```python
def start_bootstrap(
    self, scope: ProjectionScope, projector_id: str, *, operator_id: str, reason: str
) -> str: ...
```

- [ ] **Step 1: Write no-history-backfill test**

Capture counts of historical Outbox Envelopes and Deliveries, bootstrap a registered disabled `search_index` adapter, then assert both historical counts are byte-for-byte unchanged. Its historical Projection records must nevertheless equal Canon Manifest through initial W.

- [ ] **Step 2: Write the activation race test**

Coordinate two processes with barriers:

```text
bootstrap reconciles W
Canon commits W+1 while Projector disabled
activation transaction locks project row and reads H=W+1
activation_after_position=H
Canon commits a later position K>H
```

Assert W+1 has no Envelope and is covered only by activation-gap Canon replay; K has exactly one Envelope and one Delivery. Scanner cannot claim K while Partition is catching_up. After gap reconciliation, K processes and lag reaches zero.

- [ ] **Step 3: Run tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\integration\canonical\test_projector_bootstrap.py tests\integration\canonical\test_projector_activation_race.py -q
```

- [ ] **Step 4: Implement atomic activation**

Inside one PostgreSQL transaction using the same `CanonicalProject FOR UPDATE` lock as Commit Service:

```python
H = project.next_stream_position
partition.enrollment_status = "active"
partition.runtime_status = "catching_up"
partition.activation_after_position = H
run.activation_after_position = H
```

Commit Service uses `new_position > activation_after_position`. Replay `W < position <= H` directly from Canon, reconcile through H, then switch runtime active and expose pending Deliveries `>H`.

- [ ] **Step 5: Test reconciliation failure after activation**

New Envelopes may accumulate but remain unclaimed while runtime is catching_up. Recovery must resume Canon gap replay; it must never repair the gap by inserting historical Envelopes.

- [ ] **Step 6: Run tests and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\integration\canonical\test_projector_bootstrap.py tests\integration\canonical\test_projector_activation_race.py -q
git add app\canonical\projection_rebuild.py app\canonical\commit_service.py tests\integration\canonical\test_projector_bootstrap.py tests\integration\canonical\test_projector_activation_race.py
git commit -m "feat: bootstrap new projectors without historical envelopes"
```

### Task 13: Add best-effort Celery wake-up and independent PostgreSQL scanner

**Files:**

- Create: `app/projection_tasks.py`
- Create: `app/canonical/projection_cli.py`
- Create: `app/canonical/projection_health.py`
- Modify: `app/celery_app.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `app/writing/canonical_subsection_runtime.py`
- Create: `tests/unit/canonical/test_projection_wakeup.py`
- Create: `tests/unit/canonical/test_projection_health.py`
- Create: `tests/integration/canonical/test_projection_scanner_recovery.py`

**Interfaces:**

- Produces Celery task `wake_projection_scanner` and CLI commands `scan`, `drain`, `status`, `rebuild`, `bootstrap`, `requeue`.
- Both Celery and CLI construct the same Worker/factory and call `scan_once()`.

```python
class ProjectionHealthSnapshot(FrozenArtifact):
    lag_events: int
    lag_seconds: float
    oldest_pending_age_seconds: float
    processing_count: int
    expired_lease_count: int
    dead_letter_count: int
    retry_count: int
    rebuild_status_counts: dict[str, int]
    reconciliation_mismatch_count: int
    wakeup_failure_count: int
```

- [ ] **Step 1: Write wake-up loss/duplication tests**

- Make `.delay()` raise Redis connection error after a successful Canon commit; Commit result remains successful and Delivery remains pending.
- Invoke the wake task 50 times; final semantic records and published Delivery counts remain one per Envelope.
- Assert task arguments are only optional scope hints and cannot bypass claim by Delivery/event ID.

- [ ] **Step 2: Write the mandatory Celery outage recovery test**

Stop or replace Celery/Redis broker wake-up with a failing sender, create multiple Canon commits, then launch only the independent scanner. Do not replay old Celery messages. Assert every applicable Projection reaches current Canon revision and lag zero after sink Redis becomes available.

- [ ] **Step 3: Write database-derived health/status tests**

Create fixtures with one pending Delivery, one expired processing Delivery, one Dead-letter, one retry Attempt, one active Rebuild Run and one reconciliation mismatch. Assert every `ProjectionHealthSnapshot` count, Canon-to-cursor lag and oldest-pending age exactly. Also assert changing Celery queue length cannot change the snapshot.

- [ ] **Step 4: Run tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_wakeup.py tests\unit\canonical\test_projection_health.py tests\integration\canonical\test_projection_scanner_recovery.py -q
```

- [ ] **Step 5: Implement fail-open wake-up, fail-closed database scanning**

Call wake-up only after PostgreSQL commit. Catch broker publish failures, emit structured metrics/logs, and return the original Canon result. The scanner polls PostgreSQL on `PROJECTION_SCAN_INTERVAL_MS`, uses bounded `PROJECTION_SCAN_BATCH_SIZE`, and applies no correctness logic from Celery result state.

- [ ] **Step 6: Implement database-derived health/status**

Build every health field from Canon Head, Partition Cursor, Delivery, Attempt, Rebuild Run and Reconciliation rows. Never use Celery queue length as lag. `projection_cli status` prints bounded JSON without drafts, prompts, credentials or sink payloads.

- [ ] **Step 7: Add independent Docker scanner service**

Add `projection-scanner` with command:

```text
python -m app.canonical.projection_cli scan --continuous
```

It mounts the same `/data`, `/chroma`, and `/output` derived stores and depends on PostgreSQL health. It must start/retry even if Redis is unavailable; do not make broker health a process-liveness prerequisite.

- [ ] **Step 8: Run tests and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_wakeup.py tests\unit\canonical\test_projection_health.py tests\integration\canonical\test_projection_scanner_recovery.py -q
git add app\projection_tasks.py app\canonical\projection_cli.py app\canonical\projection_health.py app\celery_app.py app\config.py .env.example docker-compose.yml app\writing\canonical_subsection_runtime.py tests\unit\canonical\test_projection_wakeup.py tests\unit\canonical\test_projection_health.py tests\integration\canonical\test_projection_scanner_recovery.py
git commit -m "feat: make Celery an optional projection wakeup"
```

### Task 14: Build the real P3A fault-injection Gate and evidence verifier

**Files:**

- Create: `scripts/p3a/__init__.py`
- Create: `scripts/p3a/run_projection_gate.py`
- Create: `scripts/p3a/verify_projection_gate.py`
- Create: `tests/unit/test_p3a_gate_verifier.py`
- Create: `tests/integration/canonical/test_p3a_postgres_gate.py`
- Create: `reports/p3a/p3a-gate-evidence.json`

**Interfaces:**

- Produces evidence schema `p3a-projection-gate-v1` and fail-closed verifier.

- [ ] **Step 1: Write verifier tests before the runner**

The verifier must reject evidence when:

- backend is not PostgreSQL;
- any PostgreSQL test was skipped;
- Redis/Celery outage recovery was not run;
- any of seven Projectors lacks delete/rebuild/reconcile evidence;
- any final lag is nonzero;
- stale-token, ordering, Dead-letter/requeue or activation-race proof is absent;
- secret scan reports a finding;
- expected and actual Manifest digests differ.

- [ ] **Step 2: Run verifier tests and verify Red**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_p3a_gate_verifier.py -q
```

- [ ] **Step 3: Implement deterministic Gate runner**

The runner uses `TEST_CANONICAL_DATABASE_URL` ending in `_test`, a disposable Redis DB/namespace and temporary Chroma/Markdown roots. It records only IDs, hashes, counts, durations, error classes and command exit codes; never API keys, prompts or full drafts.

Execute and record these named scenarios:

```text
lease_contention
strict_partition_order
expired_lease_reclaim
stale_token_rejected
duplicate_wakeup_50x
celery_redis_outage_scanner_recovery
critical_dead_letter_barrier
nonblocking_dead_letter_degraded
audited_operator_requeue
seven_projection_delete_rebuild
rebuild_crash_resume
canon_commits_during_rebuild
reconciliation_missing_extra_corrupt
new_projector_no_history_backfill
activation_gap_race
final_lag_zero
```

- [ ] **Step 4: Run real PostgreSQL Gate**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\integration\canonical\test_p3a_postgres_gate.py -q
& .\.venv\Scripts\python.exe scripts\p3a\run_projection_gate.py --output reports\p3a\p3a-gate-evidence.json
& .\.venv\Scripts\python.exe scripts\p3a\verify_projection_gate.py --evidence reports\p3a\p3a-gate-evidence.json
```

Expected: zero skipped PostgreSQL scenarios and verifier prints `{"passed": true, "errors": []}`.

- [ ] **Step 5: Commit**

```powershell
git add scripts\p3a tests\unit\test_p3a_gate_verifier.py tests\integration\canonical\test_p3a_postgres_gate.py reports\p3a\p3a-gate-evidence.json
git commit -m "test: prove P3A projection recovery gate"
```

### Task 15: Rollout, rollback, runbook and final Foundation regression

**Files:**

- Create: `docs/runbooks/p3a-projection-operations.md`
- Create: `reports/p3a/p3a-gate-summary.md`
- Modify: `docs/architecture/narrative-os-architecture-status.md`
- Modify: `docs/architecture/foundation-data-ownership-v0.md`
- Modify: `docs/PROGRESS.md`
- Modify: `docs/DEPLOY.md`
- Modify: `scripts/foundation/run_golden_slice.py`
- Modify: `scripts/foundation/verify_foundation_gate.py`

**Interfaces:**

- Produces operator commands and final evidence-linked architecture status.
- Updates Foundation evidence reader for Delivery authority without weakening original Canon checks.

- [ ] **Step 1: Write the runbook from actual CLI behavior**

Document exact commands and refusal conditions for:

- inspect lag/Partition/Dead-letter;
- bounded drain;
- audited requeue;
- maintenance rebuild;
- bootstrap disabled Projector;
- resume failed run;
- stop P3A scanner before application rollback;
- verify scope before destructive Projection clear;
- recover with Redis/Celery offline;
- confirm Canon was never deleted or modified.

- [ ] **Step 2: Perform cutover and rollback drill**

Cutover order:

```text
Alembic expand/backfill
→ stop P2 synchronous dispatcher processes
→ deploy P3A app code
→ start projection scanner
→ run bounded drain
→ verify critical Barrier and lag
```

Rollback order:

```text
stop P3A scanner and Celery wake task
→ verify no processing Delivery has a live Lease
→ roll back app code while retaining expanded schema/evidence
→ enable P2 compatibility path only after P3A consumers are stopped
```

Never run P2 Dispatcher and P3A Scanner concurrently.

- [ ] **Step 3: Run full regression with fresh counts**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit tests\integration tests\contract -q
& .\.venv\Scripts\python.exe -m pytest tests\integration\canonical -m postgres -q
& .\.venv\Scripts\python.exe scripts\foundation\run_golden_slice.py --fixture tests\fixtures\foundation_golden_slice_v1.json --database-url $env:TEST_CANONICAL_DATABASE_URL
& .\.venv\Scripts\python.exe scripts\foundation\verify_foundation_gate.py --evidence reports\foundation\p2-golden-slice-evidence.json
& .\.venv\Scripts\python.exe scripts\p3a\verify_projection_gate.py --evidence reports\p3a\p3a-gate-evidence.json
& .\.venv\Scripts\ruff.exe check app tests scripts
git diff --check
git status --short
```

Any skipped PostgreSQL P3A Gate test means P3A is incomplete. Save exact passed/failed/skipped counts and evidence hashes in `reports/p3a/p3a-gate-summary.md`.

- [ ] **Step 4: Update architecture facts only after every Gate passes**

Set:

```text
P0      DONE
P1      DONE
P2      DONE
P3A     DONE
P3B     READY / still required for external Alpha
P4      UNBLOCKED BY P3A
```

Preserve the permanent statement `internal_required != external production ready` and list Deferred Online Generation Rebuild, same-stream unordered parallelism and Outbox fan-out normalization.

- [ ] **Step 5: Commit, review and branch completion**

```powershell
git add docs\runbooks\p3a-projection-operations.md reports\p3a\p3a-gate-summary.md docs\architecture\narrative-os-architecture-status.md docs\architecture\foundation-data-ownership-v0.md docs\PROGRESS.md docs\DEPLOY.md scripts\foundation
git commit -m "docs: record P3A projection gate and operations"
```

Then use `superpowers:requesting-code-review`; resolve findings with `superpowers:receiving-code-review`; rerun this task's complete verification; finally use `superpowers:finishing-a-development-branch` to choose local merge/push/keep/discard.

---

## 2. Required PostgreSQL failure matrix

| Failure point | Required durable result |
|---|---|
| Canon transaction before Delivery insert | zero partial Canon/Envelope/Delivery and unchanged stream counter |
| Canon transaction after Delivery insert | entire transaction rolls back |
| Celery publish after Canon commit | Canon/Envelope/Delivery remain; scanner recovers |
| Worker after claim, before sink | Lease expires; same Delivery retries |
| Sink write succeeds, before receipt | deterministic reapply, one semantic result |
| Receipt built, before DB publish | deterministic reapply, one semantic result |
| Old Worker completes after Lease reclaim | stale token update affects zero rows |
| Earliest Delivery retry backoff | later same-partition Delivery remains blocked |
| Earliest Delivery Dead-letter | later same-partition Delivery remains blocked until audited requeue |
| Critical Dead-letter | Canon committed, Barrier not current |
| Non-blocking Dead-letter | Canon/critical Barrier ready, health degraded |
| Rebuild after pause request | no new claim; existing shared write drains before clear |
| Rebuild after clear | resume/restart replays Canon, never reads deleted Projection as source |
| Rebuild after batch apply before checkpoint | repeats batch idempotently |
| Reconciliation mismatch | maintenance retained, Cursor unchanged, Canon untouched |
| Canon commit after Watermark | new Delivery waits and is consumed after Rebuild |
| Bootstrap before activation | no historical Envelope/Delivery created |
| Commit between W and activation Head H | covered by Canon gap replay only |
| Commit position greater than activation threshold H | exactly one Envelope + Delivery |
| Redis/Celery prolonged outage | Canon/Delivery durable; independent scanner eventually catches up |

## 3. Recommended commit sequence

1. `feat: define P3A projection contracts and schema`
2. `feat: migrate P2 outbox state into P3A deliveries`
3. `feat: create projection deliveries in canonical commits`
4. `refactor: build projection messages from Canon`
5. `feat: add ordered projection leases and dead letters`
6. `feat: execute projections through fenced lease workers`
7. `feat: gate canonical reads on projection deliveries`
8. `feat: make critical projections idempotent and inspectable`
9. `feat: add rebuildable nonblocking projections`
10. `feat: reconcile projections with canonical manifests`
11. `feat: rebuild projections from Canon with checkpoints`
12. `feat: bootstrap new projectors without historical envelopes`
13. `feat: make Celery an optional projection wakeup`
14. `test: prove P3A projection recovery gate`
15. `docs: record P3A projection gate and operations`

Each commit must contain its focused tests and pass them before proceeding. Do not accumulate all schema/runtime/adapter work into one review unit.

## 4. Self-review coverage matrix

| Frozen design requirement | Implementation task |
|---|---|
| PostgreSQL-only scheduling truth | 1, 2, 5, 6, 13 |
| P2 Envelope immutable, one-to-one Delivery | 1, 2, 3 |
| Lease/fencing/heartbeat/crash window | 5, 6, 14 |
| Strict same-partition order | 5, 14 |
| Retry/Dead-letter/audited requeue | 5, 7, 14 |
| Criticality-aware Barrier | 7 |
| Seven idempotent Projectors | 8, 9 |
| Uniform Manifest/Reconciliation | 10 |
| Durable run + checkpoint, no rebuild items | 11 |
| Projection-scoped pause and maintenance lock | 6, 11 |
| Canon continues after Watermark | 11 |
| New Projector no historical Envelope backfill | 12 |
| `activation_after_position=H`, fan-out `>H` | 3, 12 |
| Celery optional, scanner recovery | 13, 14 |
| PostgreSQL-derived lag, health and rebuild status | 5, 10, 11, 13, 14 |
| Redis/Chroma restart and rebuild | 8, 9, 11, 14 |
| Real PostgreSQL Gate and secret-free evidence | 14, 15 |
| Rollout/rollback and Foundation regression | 15 |
| Online shadow rebuild explicitly absent | Global Constraints, 15 |
| P3B/P4 stage boundaries | 15 |

Placeholder scan requirement: the finished plan must contain no unresolved placeholder markers or undefined interface names. All later-task public names are introduced in an earlier task's Interfaces block.

## 5. Completion definition

P3A is complete only when all 15 tasks are committed, all focused tests are green, the full Foundation suite still passes, the real PostgreSQL P3A Gate has zero skipped scenarios, all seven Projection Manifests reconcile after deletion/rebuild, the Redis/Celery outage test catches up using only the independent scanner, final lag is zero, and the architecture/runbook evidence is committed.

Passing P3A unlocks P4 World Runtime migration. It does not unlock external Alpha by itself; P3B remains the external-production safety gate.
