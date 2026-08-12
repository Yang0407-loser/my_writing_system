from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_engine, build_session_factory
from app.canonical.hashing import sha256_json
from app.canonical.models import ProjectionDelivery, ProjectionPartition
from app.canonical.projection_delivery import ScanFilter
from app.canonical.projection_locks import (
    ProjectionLockScope,
    ProjectionMaintenanceLocks,
    advisory_keys,
)
from app.canonical.projection_ports import ProjectionMessage, ProjectionReceipt
from app.canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from app.canonical.projection_worker import ProjectionWorker, ScanSummary
from tests.integration.canonical.helpers import build_prepared, seed_project


pytestmark = pytest.mark.postgres


def _seed(database_url):
    suffix = uuid4().hex[:10]
    tenant_id, project_id = f"tenant-{suffix}", f"project-{suffix}"
    seed_project(database_url, tenant_id, project_id, subsection_count=1)
    engine = build_engine(database_url)
    with build_session_factory(engine)() as session:
        result = CanonicalCommitService(session, tenant_id, project_id).commit(
            build_prepared(database_url, tenant_id, project_id), "lock-test"
        )
    engine.dispose()
    return tenant_id, project_id, result.commit_id


@dataclass
class SinkExecutor:
    writes: list[str]

    def __post_init__(self):
        self.spec = DEFAULT_PROJECTOR_REGISTRY.get("analytics")

    def apply(self, message: ProjectionMessage) -> ProjectionReceipt:
        self.writes.append(message.projection_event_id)
        return ProjectionReceipt(
            projection_event_id=message.projection_event_id,
            projector_id=message.projector_id,
            projector_version=self.spec.version,
            stream_position=message.stream_position,
            record_count=1,
            content_digest=sha256_json({"event": message.projection_event_id}),
        )


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (
            ProjectionLockScope("tenant-1", "project-1", "analytics"),
            (-459063789, 1092501600),
        ),
        (
            ProjectionLockScope("tenant-a", "project-b", "projector-c"),
            (1919488280, 456322974),
        ),
        (
            ProjectionLockScope("t", "p", "x"),
            (1817854640, -89171997),
        ),
    ],
)
def test_advisory_keys_are_sha256_signed_big_endian_int32(scope, expected):
    assert advisory_keys(scope) == expected


def test_different_scopes_do_not_share_a_global_lock(postgres_database_url):
    engine = build_engine(postgres_database_url)
    locks = ProjectionMaintenanceLocks(engine)
    first = ProjectionLockScope("tenant-isolation", "project-a", "analytics")
    second = ProjectionLockScope("tenant-isolation", "project-b", "analytics")
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    def hold_first():
        with locks.shared(first):
            first_entered.set()
            assert release_first.wait(timeout=5)

    def acquire_second():
        assert first_entered.wait(timeout=5)
        with locks.exclusive(second):
            second_entered.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(hold_first)
        second_future = pool.submit(acquire_second)
        assert first_entered.wait(timeout=5)
        assert second_entered.wait(timeout=1)
        release_first.set()
        first_future.result(timeout=5)
        second_future.result(timeout=5)

    engine.dispose()


def test_exclusive_maintenance_waits_until_shared_worker_exits(postgres_database_url):
    tenant_id, project_id, _ = _seed(postgres_database_url)
    engine = build_engine(postgres_database_url)
    locks = ProjectionMaintenanceLocks(engine)
    scope = ProjectionLockScope(tenant_id, project_id, "analytics")
    shared_entered = Event()
    release_shared = Event()
    exclusive_entered = Event()

    def worker_a():
        with locks.shared(scope):
            shared_entered.set()
            assert release_shared.wait(timeout=5)

    def rebuild_b():
        assert shared_entered.wait(timeout=5)
        with locks.exclusive(scope):
            exclusive_entered.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker_future = pool.submit(worker_a)
        rebuild_future = pool.submit(rebuild_b)
        assert shared_entered.wait(timeout=5)
        assert not exclusive_entered.wait(timeout=0.25)
        release_shared.set()
        worker_future.result(timeout=5)
        rebuild_future.result(timeout=5)

    assert exclusive_entered.is_set()
    engine.dispose()


def test_worker_rechecks_pause_after_waiting_for_shared_lock(postgres_database_url):
    tenant_id, project_id, commit_id = _seed(postgres_database_url)
    engine = build_engine(postgres_database_url)
    factory = build_session_factory(engine)
    scope = ProjectionLockScope(tenant_id, project_id, "analytics")
    locks = ProjectionMaintenanceLocks(engine)
    exclusive_entered = Event()
    release_exclusive = Event()
    executor = SinkExecutor([])

    def maintenance():
        with locks.exclusive(scope):
            exclusive_entered.set()
            assert release_exclusive.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        maintenance_future = pool.submit(maintenance)
        assert exclusive_entered.wait(timeout=5)
        scan_future = pool.submit(
            ProjectionWorker(
                factory,
                {"analytics": executor},
                worker_id="waiting-worker",
            ).scan_once,
            ScanFilter(
                tenant_id=tenant_id,
                project_id=project_id,
                projector_id="analytics",
                commit_id=commit_id,
                limit=1,
            ),
        )
        deadline = monotonic() + 5
        while monotonic() < deadline:
            with factory() as session:
                delivery = session.scalar(
                    select(ProjectionDelivery).where(
                        ProjectionDelivery.tenant_id == tenant_id,
                        ProjectionDelivery.project_id == project_id,
                        ProjectionDelivery.projector_id == "analytics",
                    )
                )
                if delivery.status != "processing":
                    sleep(0.01)
                    continue
                partition = session.scalar(
                    select(ProjectionPartition).where(
                        ProjectionPartition.tenant_id == tenant_id,
                        ProjectionPartition.project_id == project_id,
                        ProjectionPartition.projector_id == "analytics",
                    )
                )
                partition.runtime_status = "pause_requested"
                session.commit()
                break
        else:
            pytest.fail("worker did not claim before advisory-lock timeout")
        release_exclusive.set()
        maintenance_future.result(timeout=5)
        summary = scan_future.result(timeout=5)

    assert summary == ScanSummary(claimed=1, stale=1)
    assert executor.writes == []
    with factory() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        assert delivery.status == "processing"
        assert delivery.lease_token is not None
    engine.dispose()
