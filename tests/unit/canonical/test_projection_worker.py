from __future__ import annotations

# ruff: noqa: F401, F811 -- pytest registers the imported sibling fixture by name.

from dataclasses import dataclass

import pytest
from sqlalchemy import select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.hashing import sha256_json
from app.canonical.models import ProjectionDelivery
from app.canonical.projection_delivery import ScanFilter
from app.canonical.projection_ports import ProjectionMessage, ProjectionReceipt
from app.canonical.projection_registry import (
    DEFAULT_PROJECTOR_REGISTRY,
    ProjectorSpec,
)
from app.canonical.projection_worker import ProjectionWorker, ScanSummary
from tests.unit.canonical.test_commit_service import _prepared, canonical_session


@dataclass
class RecordingExecutor:
    projector_id: str
    receipt_mutation: dict | None = None
    return_non_receipt: bool = False

    def __post_init__(self):
        self.spec = DEFAULT_PROJECTOR_REGISTRY.get(self.projector_id)
        self.messages: list[ProjectionMessage] = []

    def apply(self, message: ProjectionMessage) -> ProjectionReceipt:
        self.messages.append(message)
        if self.return_non_receipt:
            return ReceiptImpostor(message, self.spec.version)
        receipt = {
            "projection_event_id": message.projection_event_id,
            "projector_id": message.projector_id,
            "projector_version": self.spec.version,
            "stream_position": message.stream_position,
            "record_count": 1,
            "content_digest": sha256_json({"event": message.projection_event_id}),
        }
        receipt.update(self.receipt_mutation or {})
        return ProjectionReceipt(
            **receipt
        )


class ReceiptImpostor:
    def __init__(self, message, version):
        self.projection_event_id = message.projection_event_id
        self.projector_id = message.projector_id
        self.projector_version = version
        self.stream_position = message.stream_position
        self.record_count = 1
        self.content_digest = sha256_json({"event": message.projection_event_id})

    def model_dump(self, *, mode):
        return {
            "projection_event_id": self.projection_event_id,
            "projector_id": self.projector_id,
            "projector_version": self.projector_version,
            "stream_position": self.stream_position,
            "record_count": self.record_count,
            "content_digest": self.content_digest,
        }


def _commit(session):
    return CanonicalCommitService(session, "tenant-1", "project-1").commit(
        _prepared(session), "worker-test"
    )


def test_scan_claims_replays_applies_once_and_publishes(canonical_session):
    result = _commit(canonical_session)
    executor = RecordingExecutor("analytics")
    worker = ProjectionWorker(
        lambda: canonical_session,
        {"analytics": executor},
        worker_id="unit-worker",
    )

    summary = worker.scan_once(
        ScanFilter(
            tenant_id="tenant-1",
            project_id="project-1",
            projector_id="analytics",
            commit_id=result.commit_id,
            limit=1,
        )
    )

    assert summary == ScanSummary(claimed=1, published=1)
    assert len(executor.messages) == 1
    assert executor.messages[0].commit_id == result.commit_id
    delivery = canonical_session.scalar(
        select(ProjectionDelivery).where(
            ProjectionDelivery.projector_id == "analytics"
        )
    )
    assert delivery.status == "published"
    assert delivery.receipt_json == {
        "projection_event_id": executor.messages[0].projection_event_id,
        "projector_id": "analytics",
        "projector_version": "v1",
        "stream_position": 1,
        "record_count": 1,
        "content_digest": sha256_json(
            {"event": executor.messages[0].projection_event_id}
        ),
    }


def test_scan_never_uses_dead_letter_as_eligibility(canonical_session):
    _commit(canonical_session)
    delivery = canonical_session.scalar(
        select(ProjectionDelivery).where(
            ProjectionDelivery.projector_id == "analytics"
        )
    )
    delivery.status = "dead_letter"
    canonical_session.commit()
    executor = RecordingExecutor("analytics")

    summary = ProjectionWorker(
        lambda: canonical_session,
        {"analytics": executor},
        worker_id="unit-worker",
    ).scan_once(
        ScanFilter(
            tenant_id="tenant-1",
            project_id="project-1",
            projector_id="analytics",
        )
    )

    assert summary == ScanSummary()
    assert executor.messages == []


def test_missing_executor_records_retry_without_sink_write(canonical_session):
    _commit(canonical_session)

    summary = ProjectionWorker(
        lambda: canonical_session,
        {},
        worker_id="unit-worker",
    ).scan_once(
        ScanFilter(
            tenant_id="tenant-1",
            project_id="project-1",
            projector_id="analytics",
            limit=1,
        )
    )

    assert summary == ScanSummary(claimed=1, retried=1)
    delivery = canonical_session.scalar(
        select(ProjectionDelivery).where(
            ProjectionDelivery.projector_id == "analytics"
        )
    )
    assert delivery.status == "pending"
    assert delivery.last_error_class == "LookupError"


@pytest.mark.parametrize(
    ("mismatch", "value"),
    [
        ("projector_id", "other-projector"),
        ("version", "v2"),
        ("barrier_kind", "critical"),
    ],
)
def test_executor_spec_must_match_authoritative_message_before_apply(
    canonical_session, mismatch, value
):
    _commit(canonical_session)
    executor = RecordingExecutor("analytics")
    authoritative = executor.spec
    executor.spec = ProjectorSpec(
        value if mismatch == "projector_id" else authoritative.projector_id,
        value if mismatch == "version" else authoritative.version,
        value if mismatch == "barrier_kind" else authoritative.barrier_kind,
        authoritative.retry,
    )

    summary = ProjectionWorker(
        lambda: canonical_session,
        {"analytics": executor},
        worker_id="unit-worker",
    ).scan_once(
        ScanFilter(
            tenant_id="tenant-1",
            project_id="project-1",
            projector_id="analytics",
            limit=1,
        )
    )

    assert summary == ScanSummary(claimed=1, retried=1)
    assert executor.messages == []
    delivery = canonical_session.scalar(
        select(ProjectionDelivery).where(
            ProjectionDelivery.projector_id == "analytics"
        )
    )
    assert delivery.status == "pending"
    assert delivery.receipt_json is None


@pytest.mark.parametrize(
    "executor",
    [
        RecordingExecutor("analytics", return_non_receipt=True),
        RecordingExecutor(
            "analytics", receipt_mutation={"projection_event_id": "wrong-event"}
        ),
        RecordingExecutor(
            "analytics", receipt_mutation={"projector_id": "wrong-projector"}
        ),
        RecordingExecutor(
            "analytics", receipt_mutation={"projector_version": "v2"}
        ),
        RecordingExecutor("analytics", receipt_mutation={"stream_position": 99}),
    ],
    ids=["non-receipt", "event", "projector", "version", "position"],
)
def test_invalid_receipt_never_publishes(canonical_session, executor):
    _commit(canonical_session)

    summary = ProjectionWorker(
        lambda: canonical_session,
        {"analytics": executor},
        worker_id="unit-worker",
    ).scan_once(
        ScanFilter(
            tenant_id="tenant-1",
            project_id="project-1",
            projector_id="analytics",
            limit=1,
        )
    )

    assert summary == ScanSummary(claimed=1, retried=1)
    delivery = canonical_session.scalar(
        select(ProjectionDelivery).where(
            ProjectionDelivery.projector_id == "analytics"
        )
    )
    assert delivery.status == "pending"
    assert delivery.receipt_json is None
