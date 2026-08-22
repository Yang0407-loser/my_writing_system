# P3A Projection Operations

## Inspect

```bash
python -m app.canonical.projection_cli status
python -m app.canonical.projection_cli status --tenant-id TENANT --project-id PROJECT --projector-id analytics
```

The JSON is derived from PostgreSQL Canon, Delivery, Attempt, Partition,
Rebuild and Reconciliation rows. Redis/Celery queue depth is not correctness
state. Non-zero dead letters, expired leases or reconciliation mismatches
require investigation before declaring a projection healthy.

## Drain and recovery

```bash
python -m app.canonical.projection_cli drain --tenant-id TENANT --project-id PROJECT --max-events 1000
python -m app.canonical.projection_cli requeue --delivery-id DELIVERY --operator-id OPERATOR --reason "approved recovery"
```

Requeue requires a non-blank operator and reason and appends an audit row. It
never rewrites Canon. A bounded drain may be repeated; Delivery claims are
fenced and idempotent.

## Rebuild and bootstrap

```bash
python -m app.canonical.projection_cli rebuild --tenant-id TENANT --project-id PROJECT --projector-id analytics --operator-id OPERATOR --reason "repair"
python -m app.canonical.projection_cli bootstrap --tenant-id TENANT --project-id PROJECT --projector-id search_index --operator-id OPERATOR --reason "enroll projector"
```

`bootstrap` requires a registered projector and disabled Partition. History is
replayed from Canon without creating historical Envelopes. Activation freezes
`activation_after_position=H`; positions `<=H` use Canon gap replay and only
positions `>H` receive new Envelopes.

## Rollout and rollback

1. Apply Alembic expand/backfill migrations.
2. Stop legacy P2 synchronous dispatchers.
3. Deploy P3A application code.
4. Start `projection-scanner` and run a bounded drain.
5. Verify critical Barrier readiness and `status` lag.

Rollback: stop `projection-scanner` and the Celery projection wake task, verify
no processing Delivery has a live lease, roll back application code while
retaining expanded schema/evidence, then re-enable P2 compatibility only after
P3A consumers stop. Do not run P2 Dispatcher and P3A Scanner concurrently.

Redis/Celery may remain offline during recovery: Canon commits and Delivery
rows remain durable, and the independent PostgreSQL scanner catches up. Before
destructive Projection clear, verify tenant/project/projector scope. Never
delete or modify Canon revisions, state versions, commits or ledger rows.
