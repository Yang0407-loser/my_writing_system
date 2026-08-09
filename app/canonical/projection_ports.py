"""Small, immutable boundary between the canonical outbox and projections."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import Field

from .contracts import FrozenArtifact


class ProjectionMessage(FrozenArtifact):
    """A durable canonical event rendered for exactly one projection."""

    event_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    commit_id: str = Field(min_length=1)
    projection_name: str = Field(min_length=1)
    barrier_kind: str = Field(pattern="^(critical|non_blocking)$")
    event_type: str = Field(min_length=1)
    payload: dict[str, Any]


class ProjectionPort(Protocol):
    def __call__(self, message: ProjectionMessage) -> None:
        """Apply one idempotently identified canonical event."""

