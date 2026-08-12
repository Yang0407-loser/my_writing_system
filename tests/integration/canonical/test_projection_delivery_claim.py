from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_engine, build_session_factory
from app.canonical.errors import (
    PermanentProjectionError,
    RetryableProjectionError,
)
from app.canonical.models import (
    OutboxEvent,
    ProjectionAttempt,
    ProjectionDelivery,
    ProjectionPartition,
    ProjectionRequeueAudit,
)
from app.canonical.projection_delivery import ProjectionDeliveryStore, ScanFilter
from app.canonical.projection_registry import (
    ProjectorRegistry,
    ProjectorSpec,
    RetryPolicy,
)
from tests.integration.canonical.helpers import build_prepared, seed_project


pytestmark = pytest.mark.postgres
UTC = timezone.utc


def _scope():
    suffix = uuid4().hex[:10]
    return f"tenant-{suffix}", f"project-{suffix}"


def _seed_commits(database_url: str, count: int = 2):
    tenant_id, project_id = _scope()
    _seed_commits_in_scope(database_url, tenant_id, project_id, count=count)
    return tenant_id, project_id


def _seed_commits_in_scope(database_url, tenant_id, project_id, *, count=1):
    seed_project(database_url, tenant_id, project_id, subsection_count=count)
    for position in range(1, count + 1):
        prepared = build_prepared(
            database_url,
            tenant_id,
            project_id,
            ordinal=position,
            attempt_id=f"delivery-{position}",
        )
        engine = build_engine(database_url)
        with build_session_factory(engine)() as session:
            CanonicalCommitService(session, tenant_id, project_id).commit(
                prepared, f"delivery-{position}"
            )
        engine.dispose()


def _claim(database_url, worker, scan_filter, now):
    engine = build_engine(database_url)
    try:
        with build_session_factory(engine)() as session:
            return ProjectionDeliveryStore(session).claim_next(
                worker, scan_filter, now=now
            )
    finally:
        engine.dispose()


def test_twenty_sessions_create_one_current_owner_and_token(postgres_database_url):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    now = datetime.now(UTC)
    scan_filter = ScanFilter(
        tenant_id=tenant_id, project_id=project_id, projector_id="analytics"
    )

    with ThreadPoolExecutor(max_workers=20) as pool:
        claims = list(
            pool.map(
                lambda index: _claim(
                    postgres_database_url, f"worker-{index}", scan_filter, now
                ),
                range(20),
            )
        )

    owned = [claim for claim in claims if claim is not None]
    assert len(owned) == 1
    assert owned[0].leased_by.startswith("worker-")
    assert owned[0].lease_token

    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        mirror = session.get(OutboxEvent, owned[0].outbox_event_id)
        assert (mirror.status, mirror.attempts) == ("processing", 1)
    engine.dispose()


def test_different_projector_partitions_claim_concurrently(postgres_database_url):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    now = datetime.now(UTC)
    filters = [
        ScanFilter(tenant_id=tenant_id, project_id=project_id, projector_id=projector)
        for projector in ("analytics", "task_preview")
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda item: _claim(
                    postgres_database_url, f"worker-{item[0]}", item[1], now
                ),
                enumerate(filters),
            )
        )

    assert {claim.projector_id for claim in claims if claim} == {
        "analytics",
        "task_preview",
    }


@pytest.mark.parametrize("blocked_status", ["pending", "processing", "dead_letter"])
def test_later_position_is_blocked_by_every_unpublished_status(
    postgres_database_url, blocked_status
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=2)
    now = datetime.now(UTC)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        first = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
                ProjectionDelivery.stream_position == 1,
            )
        )
        first.status = blocked_status
        if blocked_status == "processing":
            first.lease_token = "held-token"
            first.leased_by = "held-worker"
            first.leased_until = now + timedelta(minutes=5)
        session.commit()
    engine.dispose()

    claim = _claim(
        postgres_database_url,
        "worker",
        ScanFilter(
            tenant_id=tenant_id,
            project_id=project_id,
            projector_id="analytics",
            commit_id=None,
        ),
        now,
    )

    if blocked_status == "pending":
        assert claim is not None and claim.stream_position == 1
    else:
        assert claim is None


def test_missing_cursor_successor_does_not_expose_a_later_delivery(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=2)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        first = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
                ProjectionDelivery.stream_position == 1,
            )
        )
        session.delete(first)
        session.commit()
    engine.dispose()

    claim = _claim(
        postgres_database_url,
        "worker",
        ScanFilter(
            tenant_id=tenant_id,
            project_id=project_id,
            projector_id="analytics",
        ),
        datetime.now(UTC),
    )

    assert claim is None


def test_expired_lease_is_reclaimed_and_old_token_is_fenced(postgres_database_url):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    scan_filter = ScanFilter(
        tenant_id=tenant_id, project_id=project_id, projector_id="analytics"
    )
    first = _claim(postgres_database_url, "old-worker", scan_filter, datetime.now(UTC))
    reclaim_at = first.leased_until + timedelta(seconds=1)
    second = _claim(postgres_database_url, "new-worker", scan_filter, reclaim_at)

    assert second is not None
    assert second.delivery_id == first.delivery_id
    assert second.lease_token != first.lease_token

    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        store = ProjectionDeliveryStore(session)
        assert store.heartbeat(first, now=reclaim_at) is False
        assert store.mark_published(first, {"sink": "old"}, now=reclaim_at) is False
        assert store.record_failure(
            first, RetryableProjectionError("old"), now=reclaim_at
        ) is False
    engine.dispose()


def test_publish_advances_cursor_and_exposes_next_position(postgres_database_url):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=2)
    scan_filter = ScanFilter(
        tenant_id=tenant_id, project_id=project_id, projector_id="analytics"
    )
    now = datetime.now(UTC)
    first = _claim(postgres_database_url, "worker", scan_filter, now)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        assert ProjectionDeliveryStore(session).mark_published(
            first, {"sink": "ok"}, now=now
        )
        partition = session.scalar(
            select(ProjectionPartition).where(
                ProjectionPartition.tenant_id == tenant_id,
                ProjectionPartition.project_id == project_id,
                ProjectionPartition.projector_id == "analytics",
            )
        )
        assert partition.last_published_position == 1
        mirror = session.get(OutboxEvent, first.outbox_event_id)
        assert mirror.status == "published"
    engine.dispose()

    second = _claim(postgres_database_url, "worker", scan_filter, now)
    assert second is not None and second.stream_position == 2


def test_publish_with_forged_attempt_id_rolls_back_every_outcome(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    now = datetime.now(UTC)
    claim = _claim(
        postgres_database_url,
        "worker",
        ScanFilter(
            tenant_id=tenant_id,
            project_id=project_id,
            projector_id="analytics",
        ),
        now,
    )
    forged = replace(claim, attempt_id=str(uuid4()))

    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        assert not ProjectionDeliveryStore(session).mark_published(
            forged, {"sink": "forged"}, now=now
        )
        delivery = session.get(ProjectionDelivery, claim.delivery_id)
        attempt = session.get(ProjectionAttempt, claim.attempt_id)
        partition = session.scalar(
            select(ProjectionPartition).where(
                ProjectionPartition.tenant_id == tenant_id,
                ProjectionPartition.project_id == project_id,
                ProjectionPartition.projector_id == "analytics",
            )
        )
        assert delivery.status == "processing"
        assert attempt.outcome == "claimed"
        assert partition.last_published_position == 0
        assert session.get(OutboxEvent, claim.outbox_event_id).status == "processing"
    engine.dispose()


def test_failure_with_forged_attempt_id_rolls_back_every_outcome(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    now = datetime.now(UTC)
    claim = _claim(
        postgres_database_url,
        "worker",
        ScanFilter(
            tenant_id=tenant_id,
            project_id=project_id,
            projector_id="analytics",
        ),
        now,
    )
    forged = replace(claim, attempt_id=str(uuid4()))

    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        assert not ProjectionDeliveryStore(session).record_failure(
            forged, RetryableProjectionError("forged"), now=now
        )
        delivery = session.get(ProjectionDelivery, claim.delivery_id)
        attempt = session.get(ProjectionAttempt, claim.attempt_id)
        assert delivery.status == "processing"
        assert attempt.outcome == "claimed"
        assert session.get(OutboxEvent, claim.outbox_event_id).status == "processing"
    engine.dispose()


def test_claim_binds_malicious_projector_id_as_data(postgres_database_url):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    projector_id = "evil' OR 1=1 --"
    retry = RetryPolicy(3, 2, 30, 120, 30)
    registry = ProjectorRegistry(
        [ProjectorSpec(projector_id, "v-malicious", "non_blocking", retry)]
    )
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        envelope = session.get(OutboxEvent, session.scalar(
            select(ProjectionDelivery.outbox_event_id).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        ))
        envelope.projection_name = projector_id
        session.execute(
            ProjectionDelivery.__table__.update()
            .where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
            .values(projector_id=projector_id, projector_version="v-malicious")
        )
        session.execute(
            ProjectionPartition.__table__.update()
            .where(
                ProjectionPartition.tenant_id == tenant_id,
                ProjectionPartition.project_id == project_id,
                ProjectionPartition.projector_id == "analytics",
            )
            .values(projector_id=projector_id, projector_version="v-malicious")
        )
        session.commit()
        claim = ProjectionDeliveryStore(session, registry).claim_next(
            "worker",
            ScanFilter(
                tenant_id=tenant_id,
                project_id=project_id,
                projector_id=projector_id,
            ),
            now=datetime.now(UTC),
        )
        assert claim is not None and claim.projector_id == projector_id
    engine.dispose()


def test_unknown_projector_registration_is_dead_lettered_without_being_claimed(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        partition = session.scalar(
            select(ProjectionPartition).where(
                ProjectionPartition.tenant_id == tenant_id,
                ProjectionPartition.project_id == project_id,
                ProjectionPartition.projector_id == "analytics",
            )
        )
        delivery.projector_version = "v-unknown"
        partition.projector_version = "v-unknown"
        session.commit()
        claimed = ProjectionDeliveryStore(session).claim_next(
            "worker",
            ScanFilter(
                tenant_id=tenant_id,
                project_id=project_id,
            ),
            now=datetime.now(UTC),
        )
        assert claimed is None or claimed.delivery_id != delivery.id
        session.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert delivery.attempt_count == 0
        assert delivery.last_error_class == "UnknownProjectorVersionError"
        mirror = session.get(OutboxEvent, delivery.outbox_event_id)
        assert mirror.status == "failed"
    engine.dispose()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("projector_version", "v-delivery-only-unknown"),
        ("projector_id", "unknown-delivery-only"),
    ],
)
def test_delivery_only_unknown_registration_is_dead_lettered_in_envelope_partition(
    postgres_database_url,
    field,
    invalid_value,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        setattr(delivery, field, invalid_value)
        session.commit()

        claimed = ProjectionDeliveryStore(session).claim_next(
            "worker",
            ScanFilter(tenant_id=tenant_id, project_id=project_id),
            now=datetime.now(UTC),
        )

        assert claimed is None or claimed.delivery_id != delivery.id
        session.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert delivery.attempt_count == 0
        assert delivery.last_error_class == "UnknownProjectorVersionError"
        assert session.scalar(
            select(func.count()).select_from(ProjectionAttempt).where(
                ProjectionAttempt.delivery_id == delivery.id
            )
        ) == 0
        partition = session.scalar(
            select(ProjectionPartition).where(
                ProjectionPartition.tenant_id == tenant_id,
                ProjectionPartition.project_id == project_id,
                ProjectionPartition.projector_id == "analytics",
            )
        )
        assert partition.enrollment_status == "active"
        assert partition.runtime_status == "active"
    engine.dispose()


def test_projector_filter_routes_by_envelope_not_corrupted_delivery_id(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        delivery.projector_id = "spoof-projector"
        session.commit()
        store = ProjectionDeliveryStore(session)

        assert store.claim_next(
            "spoof-worker",
            ScanFilter(
                tenant_id=tenant_id,
                project_id=project_id,
                projector_id="spoof-projector",
            ),
            now=datetime.now(UTC),
        ) is None
        session.refresh(delivery)
        assert delivery.status == "pending"

        assert store.claim_next(
            "analytics-worker",
            ScanFilter(
                tenant_id=tenant_id,
                project_id=project_id,
                projector_id="analytics",
            ),
            now=datetime.now(UTC),
        ) is None
        session.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert delivery.attempt_count == 0
    engine.dispose()


def test_registered_projector_id_cannot_spoof_another_envelope_partition(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        conflicting = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "task_preview",
            )
        )
        session.delete(conflicting)
        session.flush()
        delivery.projector_id = "task_preview"
        session.commit()

        claim = ProjectionDeliveryStore(session).claim_next(
            "worker",
            ScanFilter(
                tenant_id=tenant_id,
                project_id=project_id,
                projector_id="analytics",
            ),
            now=datetime.now(UTC),
        )

        assert claim is None
        session.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert delivery.attempt_count == 0
    engine.dispose()


def test_original_scope_cleanup_does_not_block_unrelated_valid_claim(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        conflicting = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "task_preview",
            )
        )
        session.delete(conflicting)
        session.flush()
        delivery.projector_id = "task_preview"
        session.commit()

        claim = ProjectionDeliveryStore(session).claim_next(
            "worker",
            ScanFilter(tenant_id=tenant_id, project_id=project_id),
            now=datetime.now(UTC),
        )

        session.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert claim is not None
        assert claim.delivery_id != delivery.id
        assert claim.projector_id != "analytics"
    engine.dispose()


def test_barrier_kind_mismatch_is_dead_lettered_without_claim(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        delivery.barrier_kind = "critical"
        session.commit()

        claim = ProjectionDeliveryStore(session).claim_next(
            "worker",
            ScanFilter(
                tenant_id=tenant_id,
                project_id=project_id,
                projector_id="analytics",
            ),
            now=datetime.now(UTC),
        )

        assert claim is None
        session.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert delivery.attempt_count == 0
    engine.dispose()


@pytest.mark.parametrize("spoof_tenant", [False, True], ids=["project", "tenant"])
def test_delivery_scope_spoof_is_routed_and_filtered_by_envelope(
    postgres_database_url,
    spoof_tenant,
):
    original_tenant, original_project = _seed_commits(
        postgres_database_url, count=1
    )
    target_tenant = f"tenant-{uuid4().hex[:10]}" if spoof_tenant else original_tenant
    target_project = f"project-{uuid4().hex[:10]}"
    _seed_commits_in_scope(
        postgres_database_url,
        target_tenant,
        target_project,
        count=1,
    )
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == original_tenant,
                ProjectionDelivery.project_id == original_project,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        target_delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == target_tenant,
                ProjectionDelivery.project_id == target_project,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        session.delete(target_delivery)
        session.flush()
        delivery.tenant_id = target_tenant
        delivery.project_id = target_project
        session.commit()
        store = ProjectionDeliveryStore(session)

        assert store.claim_next(
            "spoof-scope-worker",
            ScanFilter(
                tenant_id=target_tenant,
                project_id=target_project,
                projector_id="analytics",
            ),
            now=datetime.now(UTC),
        ) is None
        session.refresh(delivery)
        assert delivery.status == "pending"

        claim = store.claim_next(
            "original-scope-worker",
            ScanFilter(
                tenant_id=original_tenant,
                project_id=original_project,
            ),
            now=datetime.now(UTC),
        )

        session.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert delivery.attempt_count == 0
        assert claim is not None
        assert claim.delivery_id != delivery.id
    engine.dispose()


def test_registry_barrier_mismatch_is_dead_lettered_and_other_partition_progresses(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        envelope = session.get(OutboxEvent, delivery.outbox_event_id)
        delivery.barrier_kind = "critical"
        envelope.barrier_kind = "critical"
        session.commit()

        claim = ProjectionDeliveryStore(session).claim_next(
            "worker",
            ScanFilter(tenant_id=tenant_id, project_id=project_id),
            now=datetime.now(UTC),
        )

        session.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert delivery.attempt_count == 0
        assert claim is not None
        assert claim.delivery_id != delivery.id
    engine.dispose()


def test_empty_registry_dead_letters_applicable_pending_delivery_without_claim(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )

        claim = ProjectionDeliveryStore(session, ProjectorRegistry(())).claim_next(
            "worker",
            ScanFilter(
                tenant_id=tenant_id,
                project_id=project_id,
                projector_id="analytics",
            ),
            now=datetime.now(UTC),
        )

        assert claim is None
        session.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert delivery.attempt_count == 0
        assert delivery.last_error_class == "UnknownProjectorVersionError"
        assert session.scalar(
            select(func.count()).select_from(ProjectionAttempt).where(
                ProjectionAttempt.delivery_id == delivery.id
            )
        ) == 0
        assert session.get(OutboxEvent, delivery.outbox_event_id).status == "failed"
    engine.dispose()


@pytest.mark.parametrize(
    ("enrollment_status", "runtime_status"),
    [("disabled", "active"), ("active", "maintenance")],
)
def test_invalid_delivery_is_not_dead_lettered_outside_active_partition_context(
    postgres_database_url,
    enrollment_status,
    runtime_status,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        partition = session.scalar(
            select(ProjectionPartition).where(
                ProjectionPartition.tenant_id == tenant_id,
                ProjectionPartition.project_id == project_id,
                ProjectionPartition.projector_id == "analytics",
            )
        )
        delivery.projector_version = "v-unknown"
        partition.enrollment_status = enrollment_status
        partition.runtime_status = runtime_status
        session.commit()

        ProjectionDeliveryStore(session).claim_next(
            "worker",
            ScanFilter(
                tenant_id=tenant_id,
                project_id=project_id,
                projector_id="analytics",
            ),
            now=datetime.now(UTC),
        )

        session.refresh(delivery)
        assert delivery.status == "pending"
        assert delivery.attempt_count == 0
    engine.dispose()


def test_heartbeat_dead_letters_corrupted_processing_without_extending_lease(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    now = datetime.now(UTC)
    claim = _claim(
        postgres_database_url,
        "worker",
        ScanFilter(
            tenant_id=tenant_id,
            project_id=project_id,
            projector_id="analytics",
        ),
        now,
    )
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.get(ProjectionDelivery, claim.delivery_id)
        original_lease = delivery.leased_until
        delivery.barrier_kind = "critical"
        session.commit()
        store = ProjectionDeliveryStore(session)

        assert store.heartbeat(claim, now=now) is False

        session.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert delivery.leased_until is None
        assert delivery.lease_token is None
        assert delivery.last_error_class == "ProjectionConflictError"
        assert original_lease is not None
        attempt = session.get(ProjectionAttempt, claim.attempt_id)
        assert attempt.outcome == "dead_lettered"
        assert attempt.finished_at == now
        assert session.get(OutboxEvent, claim.outbox_event_id).status == "failed"
    engine.dispose()


@pytest.mark.parametrize(
    "corrupt_field",
    [
        "tenant_id",
        "project_id",
        "stream_position",
        "barrier_kind",
        "projector_id",
        "projector_version",
    ],
)
def test_publish_dead_letters_post_claim_identity_corruption_without_advancing_cursor(
    postgres_database_url,
    corrupt_field,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    now = datetime.now(UTC)
    claim = _claim(
        postgres_database_url,
        "worker",
        ScanFilter(
            tenant_id=tenant_id,
            project_id=project_id,
            projector_id="analytics",
        ),
        now,
    )
    target_project = None
    if corrupt_field == "project_id":
        target_project = f"project-{uuid4().hex[:10]}"
        _seed_commits_in_scope(
            postgres_database_url,
            tenant_id,
            target_project,
            count=1,
        )

    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.get(ProjectionDelivery, claim.delivery_id)
        if corrupt_field == "tenant_id":
            delivery.tenant_id = f"tenant-corrupt-{uuid4().hex[:8]}"
        elif corrupt_field == "project_id":
            conflicting = session.scalar(
                select(ProjectionDelivery).where(
                    ProjectionDelivery.project_id == target_project,
                    ProjectionDelivery.projector_id == "analytics",
                    ProjectionDelivery.stream_position == 1,
                )
            )
            session.delete(conflicting)
            session.flush()
            delivery.project_id = target_project
        elif corrupt_field == "stream_position":
            delivery.stream_position = 99
        elif corrupt_field == "barrier_kind":
            delivery.barrier_kind = "critical"
        elif corrupt_field == "projector_id":
            delivery.projector_id = f"projector-corrupt-{uuid4().hex[:8]}"
        else:
            delivery.projector_version = "v-corrupt"
        session.commit()

        assert ProjectionDeliveryStore(session).mark_published(
            claim,
            {"sink": "must-not-acknowledge"},
            now=now,
        ) is False

        session.refresh(delivery)
        partition = session.scalar(
            select(ProjectionPartition).where(
                ProjectionPartition.tenant_id == tenant_id,
                ProjectionPartition.project_id == project_id,
                ProjectionPartition.projector_id == "analytics",
            )
        )
        attempt = session.get(ProjectionAttempt, claim.attempt_id)
        mirror = session.get(OutboxEvent, claim.outbox_event_id)
        assert delivery.status == "dead_letter"
        assert delivery.lease_token is None
        assert delivery.leased_by is None
        assert delivery.leased_until is None
        assert delivery.published_at is None
        assert delivery.receipt_json is None
        assert delivery.receipt_digest is None
        assert delivery.last_error_class == "ProjectionConflictError"
        assert attempt.outcome == "dead_lettered"
        assert attempt.finished_at == now
        assert partition.last_published_position == 0
        assert partition.last_published_event_id is None
        assert mirror.status == "failed"
        assert mirror.published_at is None
    engine.dispose()


def test_failure_dead_letters_corrupted_processing_and_finalizes_current_attempt(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    now = datetime.now(UTC)
    claim = _claim(
        postgres_database_url,
        "worker",
        ScanFilter(
            tenant_id=tenant_id,
            project_id=project_id,
            projector_id="analytics",
        ),
        now,
    )
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.get(ProjectionDelivery, claim.delivery_id)
        delivery.stream_position = 99
        session.commit()

        assert ProjectionDeliveryStore(session).record_failure(
            claim,
            RetryableProjectionError("transport failed"),
            now=now,
        ) is False

        session.refresh(delivery)
        attempt = session.get(ProjectionAttempt, claim.attempt_id)
        assert delivery.status == "dead_letter"
        assert delivery.available_at == now
        assert delivery.lease_token is None
        assert delivery.last_error_class == "ProjectionConflictError"
        assert attempt.outcome == "dead_lettered"
        assert attempt.finished_at == now
        assert attempt.retry_delay_seconds is None
        assert session.get(OutboxEvent, claim.outbox_event_id).status == "failed"
    engine.dispose()


def test_scanner_dead_letters_expired_corrupted_processing_without_forged_authority(
    postgres_database_url,
):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    now = datetime.now(UTC)
    scan_filter = ScanFilter(tenant_id=tenant_id, project_id=project_id)
    claim = _claim(
        postgres_database_url,
        "old-worker",
        replace(scan_filter, projector_id="analytics"),
        now,
    )
    scan_at = claim.leased_until + timedelta(seconds=1)
    forged = replace(claim, lease_token=uuid4().hex)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        delivery = session.get(ProjectionDelivery, claim.delivery_id)
        delivery.stream_position = 99
        session.commit()
        store = ProjectionDeliveryStore(session)

        assert store.heartbeat(forged, now=now) is False
        assert store.mark_published(forged, {"sink": "forged"}, now=now) is False
        assert store.record_failure(
            forged,
            PermanentProjectionError("forged"),
            now=now,
        ) is False
        session.refresh(delivery)
        assert delivery.status == "processing"
        assert delivery.lease_token == claim.lease_token
        assert session.get(ProjectionAttempt, claim.attempt_id).outcome == "claimed"

        unrelated = store.claim_next("scanner", scan_filter, now=scan_at)

        session.refresh(delivery)
        attempts = session.scalars(
            select(ProjectionAttempt).where(
                ProjectionAttempt.delivery_id == claim.delivery_id
            )
        ).all()
        assert delivery.status == "dead_letter"
        assert delivery.lease_token is None
        assert delivery.leased_by is None
        assert delivery.leased_until is None
        assert delivery.last_error_class == "ProjectionConflictError"
        assert len(attempts) == 1
        assert attempts[0].id == claim.attempt_id
        assert attempts[0].outcome == "dead_lettered"
        assert attempts[0].finished_at == scan_at
        assert unrelated is not None
        assert unrelated.delivery_id != claim.delivery_id
        assert store.heartbeat(claim, now=scan_at) is False
        assert store.mark_published(claim, {"sink": "late"}, now=scan_at) is False
        assert store.record_failure(
            claim,
            RetryableProjectionError("late"),
            now=scan_at,
        ) is False
    engine.dispose()


def test_failure_policy_and_audited_requeue_preserve_attempts(postgres_database_url):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    scan_filter = ScanFilter(
        tenant_id=tenant_id, project_id=project_id, projector_id="analytics"
    )
    now = datetime.now(UTC)
    claim = _claim(postgres_database_url, "worker", scan_filter, now)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        store = ProjectionDeliveryStore(session)
        assert store.record_failure(
            claim, PermanentProjectionError("invalid canon"), now=now
        )
        before = session.scalar(
            select(func.count()).select_from(ProjectionAttempt).where(
                ProjectionAttempt.delivery_id == claim.delivery_id
            )
        )
        with pytest.raises(ValueError):
            store.requeue_dead_letter(claim.delivery_id, "", "reason", now=now)
        with pytest.raises(ValueError):
            store.requeue_dead_letter(claim.delivery_id, "operator", " ", now=now)
        assert store.requeue_dead_letter(
            claim.delivery_id, "operator-7", "sink corrected", now=now
        )
        delivery = session.get(ProjectionDelivery, claim.delivery_id)
        assert delivery.status == "pending"
        assert delivery.attempt_count == 1
        assert session.scalar(
            select(func.count()).select_from(ProjectionAttempt).where(
                ProjectionAttempt.delivery_id == claim.delivery_id
            )
        ) == before
        audit = session.scalar(
            select(ProjectionRequeueAudit).where(
                ProjectionRequeueAudit.delivery_id == claim.delivery_id
            )
        )
        assert (audit.operator_id, audit.reason, audit.prior_attempt_count) == (
            "operator-7",
            "sink corrected",
            1,
        )
    engine.dispose()


def test_skip_locked_does_not_wait_on_an_unrelated_partition(postgres_database_url):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=1)
    engine = build_engine(postgres_database_url)
    blocker = build_session_factory(engine)()
    locked = blocker.scalar(
        select(ProjectionDelivery)
        .where(
            ProjectionDelivery.tenant_id == tenant_id,
            ProjectionDelivery.project_id == project_id,
            ProjectionDelivery.projector_id == "analytics",
        )
        .with_for_update()
    )
    assert locked is not None
    try:
        claim = _claim(
            postgres_database_url,
            "worker",
            ScanFilter(
                tenant_id=tenant_id,
                project_id=project_id,
                projector_id="task_preview",
            ),
            datetime.now(UTC),
        )
        assert claim is not None and claim.projector_id == "task_preview"
    finally:
        blocker.rollback()
        blocker.close()
        engine.dispose()


def test_lag_reads_partition_cursor_and_canon_head(postgres_database_url):
    tenant_id, project_id = _seed_commits(postgres_database_url, count=2)
    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        assert ProjectionDeliveryStore(session).lag(
            ScanFilter(
                tenant_id=tenant_id,
                project_id=project_id,
                projector_id="analytics",
            )
        ) == 2
    engine.dispose()
