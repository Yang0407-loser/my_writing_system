"""Immutable boundaries between Canonical replay and P3A projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Protocol

from pydantic import Field

from .contracts import FrozenArtifact
from .projection_registry import ProjectorSpec


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
        "requested",
        "pausing",
        "clearing",
        "rebuilding",
        "reconciling",
        "catching_up",
        "completed",
        "failed",
        "reconciliation_failed",
    ]
    checkpoint_position: int
    watermark_position: int
    activation_after_position: int | None = None


class ProjectionExecutor(Protocol):
    spec: ProjectorSpec

    def apply(self, message: ProjectionMessage) -> ProjectionReceipt: ...


class ProjectionAdapter(ProjectionExecutor, Protocol):
    def clear(self, scope: ProjectionScope) -> None: ...

    def expected_records(
        self, messages: Iterable[ProjectionMessage]
    ) -> tuple[ProjectionRecord, ...]: ...

    def actual_records(self, scope: ProjectionScope) -> tuple[ProjectionRecord, ...]: ...


class ProjectionPort(Protocol):
    """Deprecated P2 callable contract retained until the dispatcher cutover."""

    def __call__(self, message: ProjectionMessage) -> None: ...

