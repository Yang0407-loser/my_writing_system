from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.models import (
    ProjectionAttempt,
    ProjectionDelivery,
    ProjectionRebuildRun,
    ProjectionReconciliation,
)
from app.canonical.projection_delivery import ScanFilter
from app.canonical.projection_health import projection_health_snapshot
from app.canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from tests.unit.canonical.test_commit_service import _prepared


pytest_plugins = ("tests.fixtures_canonical",)


def test_health_snapshot_is_derived_from_database_rows(canonical_session):
    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    CanonicalCommitService(canonical_session, "tenant-1", "project-1").commit(
        _prepared(canonical_session), "health"
    )
    deliveries = canonical_session.scalars(
        select(ProjectionDelivery).order_by(ProjectionDelivery.projector_id)
    ).all()
    pending, processing, dead_letter, retry = deliveries[:4]
    pending.created_at = now - timedelta(seconds=30)
    processing.status = "processing"
    processing.lease_token = "expired-token"
    processing.leased_by = "worker-1"
    processing.leased_until = now - timedelta(seconds=5)
    processing.attempt_count = 1
    processing.created_at = now - timedelta(seconds=40)
    dead_letter.status = "dead_letter"
    dead_letter.created_at = now - timedelta(seconds=50)
    retry.attempt_count = 1
    canonical_session.add(
        ProjectionAttempt(
            id=str(uuid4()),
            delivery_id=retry.id,
            attempt_number=1,
            lease_token="retry-token",
            leased_by="worker-2",
            trigger_source="scanner",
            outcome="retry_scheduled",
            started_at=now - timedelta(seconds=20),
            finished_at=now - timedelta(seconds=19),
            error_class="ConnectionError",
            error_message="temporary",
            retry_delay_seconds=2,
        )
    )
    run = ProjectionRebuildRun(
        id=str(uuid4()),
        tenant_id="tenant-1",
        project_id="project-1",
        projector_id="analytics",
        projector_version="v1",
        run_kind="maintenance",
        status="reconciling",
        watermark_position=1,
        checkpoint_position=1,
        processed_record_count=1,
        operator_id="operator-1",
        operator_reason="health fixture",
    )
    canonical_session.add(run)
    canonical_session.add(
        ProjectionReconciliation(
            id=str(uuid4()),
            rebuild_run_id=run.id,
            tenant_id="tenant-1",
            project_id="project-1",
            projector_id="analytics",
            watermark_position=1,
            expected_manifest_json={"digest": "expected"},
            actual_manifest_json={"digest": "actual"},
            expected_digest="a" * 64,
            actual_digest="b" * 64,
            diff_summary_json={"changed": 1},
            reconciled_at=now,
        )
    )
    canonical_session.commit()

    snapshot = projection_health_snapshot(
        canonical_session,
        ScanFilter(tenant_id="tenant-1", project_id="project-1"),
        now=now,
        wakeup_failures=3,
    )

    assert snapshot.lag_events == 7
    assert snapshot.lag_seconds == 50
    assert snapshot.oldest_pending_age_seconds == 30
    assert snapshot.processing_count == 1
    assert snapshot.expired_lease_count == 1
    assert snapshot.dead_letter_count == 1
    assert snapshot.retry_count == 1
    assert snapshot.rebuild_status_counts == {"reconciling": 1}
    assert snapshot.reconciliation_mismatch_count == 1
    assert snapshot.wakeup_failure_count == 3


def test_health_snapshot_does_not_accept_celery_queue_state(canonical_session):
    parameters = projection_health_snapshot.__annotations__
    assert "celery_queue_length" not in parameters
    assert "broker" not in parameters
    assert projection_health_snapshot(canonical_session).lag_events == 0


def test_health_lag_honors_barrier_kind_filter(canonical_session):
    CanonicalCommitService(canonical_session, "tenant-1", "project-1").commit(
        _prepared(canonical_session), "health-barrier-filter"
    )
    critical_ids = {
        spec.projector_id
        for spec in DEFAULT_PROJECTOR_REGISTRY.all()
        if spec.barrier_kind == "critical"
    }

    snapshot = projection_health_snapshot(
        canonical_session,
        ScanFilter(barrier_kind="critical"),
    )

    assert snapshot.lag_events == len(critical_ids)
