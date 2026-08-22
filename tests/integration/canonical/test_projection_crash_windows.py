from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_engine, build_session_factory
from app.canonical.hashing import sha256_json
from app.canonical.models import ProjectionDelivery
from app.canonical.projection_delivery import ScanFilter
from app.canonical.projection_ports import ProjectionMessage, ProjectionReceipt
from app.canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from app.canonical.projection_worker import ProjectionWorker
from tests.integration.canonical.helpers import build_prepared, seed_project


pytestmark = pytest.mark.postgres


class InjectedTermination(BaseException):
    pass


@dataclass
class SemanticUpsertExecutor:
    records: dict[str, dict]
    terminate_after_apply: bool = False

    def __post_init__(self):
        self.spec = DEFAULT_PROJECTOR_REGISTRY.get("analytics")

    def apply(self, message: ProjectionMessage) -> ProjectionReceipt:
        self.records[message.projection_event_id] = {
            "projection_event_id": message.projection_event_id,
            "commit_id": message.commit_id,
            "stream_position": message.stream_position,
        }
        if self.terminate_after_apply:
            self.terminate_after_apply = False
            raise InjectedTermination("adapter apply before receipt")
        return ProjectionReceipt(
            projection_event_id=message.projection_event_id,
            projector_id=message.projector_id,
            projector_version=self.spec.version,
            stream_position=message.stream_position,
            record_count=1,
            content_digest=sha256_json(self.records[message.projection_event_id]),
        )


def _seed(database_url):
    suffix = uuid4().hex[:10]
    tenant_id, project_id = f"tenant-{suffix}", f"project-{suffix}"
    seed_project(database_url, tenant_id, project_id, subsection_count=1)
    engine = build_engine(database_url)
    with build_session_factory(engine)() as session:
        result = CanonicalCommitService(session, tenant_id, project_id).commit(
            build_prepared(database_url, tenant_id, project_id), "crash-window"
        )
    engine.dispose()
    return tenant_id, project_id, result.commit_id


def _expire_processing_lease(database_url, tenant_id, project_id):
    engine = build_engine(database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        assert delivery.status == "processing"
        delivery.leased_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    engine.dispose()


def _assert_recovered(database_url, tenant_id, project_id, records):
    engine = build_engine(database_url)
    with build_session_factory(engine)() as session:
        delivery = session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == tenant_id,
                ProjectionDelivery.project_id == project_id,
                ProjectionDelivery.projector_id == "analytics",
            )
        )
        assert delivery.status == "published"
        assert delivery.receipt_json["projection_event_id"] in records
        assert len(records) == 1
    engine.dispose()


@pytest.mark.parametrize(
    "window",
    [
        "claim_before_adapter_apply",
        "adapter_apply_before_receipt",
        "receipt_before_db_publish",
        "db_publish_before_stale_wakeup",
    ],
)
def test_crash_window_recovers_to_one_semantic_record_and_published_delivery(
    postgres_database_url, window
):
    tenant_id, project_id, commit_id = _seed(postgres_database_url)
    engine = build_engine(postgres_database_url)
    factory = build_session_factory(engine)
    records: dict[str, dict] = {}
    executor = SemanticUpsertExecutor(
        records, terminate_after_apply=window == "adapter_apply_before_receipt"
    )
    fired = False

    def terminate(stage):
        nonlocal fired
        target = {
            "claim_before_adapter_apply": "after_claim",
            "receipt_before_db_publish": "after_receipt",
            "db_publish_before_stale_wakeup": "after_publish",
        }.get(window)
        if not fired and stage == target:
            fired = True
            raise InjectedTermination(window)

    worker = ProjectionWorker(
        factory,
        {"analytics": executor},
        worker_id="crashing-worker",
        failure_hook=terminate,
    )
    scan_filter = ScanFilter(
        tenant_id=tenant_id,
        project_id=project_id,
        projector_id="analytics",
        commit_id=commit_id,
        limit=1,
    )

    with pytest.raises(InjectedTermination):
        worker.scan_once(scan_filter)

    if window != "db_publish_before_stale_wakeup":
        _expire_processing_lease(postgres_database_url, tenant_id, project_id)
    recovery = ProjectionWorker(
        factory,
        {"analytics": executor},
        worker_id="recovery-worker",
    ).scan_once(scan_filter)

    if window == "db_publish_before_stale_wakeup":
        assert recovery.claimed == 0
    else:
        assert recovery.claimed == 1
        assert recovery.published == 1
    _assert_recovered(postgres_database_url, tenant_id, project_id, records)
    engine.dispose()
