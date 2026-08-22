"""Immutable projector capability registry for the P3A projection runtime."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable, Literal


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
    def __init__(self, specs: Iterable[ProjectorSpec]) -> None:
        self._specs = tuple(specs)
        self._by_id = {spec.projector_id: spec for spec in self._specs}
        if len(self._by_id) != len(self._specs):
            duplicates = {
                spec.projector_id
                for spec in self._specs
                if sum(item.projector_id == spec.projector_id for item in self._specs) > 1
            }
            raise ValueError(f"duplicate projector_id: {sorted(duplicates)[0]}")

    def get(self, projector_id: str) -> ProjectorSpec:
        return self._by_id[projector_id]

    def all(self) -> tuple[ProjectorSpec, ...]:
        return self._specs


CRITICAL_RETRY = RetryPolicy(
    max_attempts=8,
    base_delay_seconds=2,
    max_delay_seconds=300,
    lease_seconds=120,
    heartbeat_seconds=30,
)
NON_BLOCKING_RETRY = RetryPolicy(
    max_attempts=5,
    base_delay_seconds=2,
    max_delay_seconds=300,
    lease_seconds=120,
    heartbeat_seconds=30,
)

BASELINE_PROJECTOR_SPECS = (
    ProjectorSpec("legacy_world_event", "v1", "critical", CRITICAL_RETRY),
    ProjectorSpec("handover_context", "v1", "critical", CRITICAL_RETRY),
    ProjectorSpec("chroma_story_chunks", "v1", "critical", CRITICAL_RETRY),
    ProjectorSpec("redis_stream", "v1", "non_blocking", NON_BLOCKING_RETRY),
    ProjectorSpec("task_preview", "v1", "non_blocking", NON_BLOCKING_RETRY),
    ProjectorSpec("markdown_export", "v1", "non_blocking", NON_BLOCKING_RETRY),
    ProjectorSpec("analytics", "v1", "non_blocking", NON_BLOCKING_RETRY),
)
DEFAULT_PROJECTOR_REGISTRY = ProjectorRegistry(BASELINE_PROJECTOR_SPECS)


def projection_event_id(projector_id: str, commit_id: str) -> str:
    """Return the stable semantic identity shared by delivery and replay."""
    return sha256(f"{projector_id}:{commit_id}".encode("utf-8")).hexdigest()
