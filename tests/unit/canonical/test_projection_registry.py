from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256
from typing import get_type_hints

import pytest

from app.canonical.projection_ports import ProjectionExecutor, ProjectionMessage
from app.canonical.projection_registry import (
    BASELINE_PROJECTOR_SPECS,
    ProjectorRegistry,
    ProjectorSpec,
    RetryPolicy,
    projection_event_id,
)


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


def test_registry_preserves_order_and_rejects_duplicate_projector_ids():
    retry = RetryPolicy(8, 2, 300, 120, 30)
    first = ProjectorSpec("first", "v1", "critical", retry)
    second = ProjectorSpec("second", "v1", "non_blocking", retry)

    registry = ProjectorRegistry((first, second))

    assert registry.all() == (first, second)
    assert registry.get("second") is second
    with pytest.raises(ValueError, match="duplicate projector_id: first"):
        ProjectorRegistry((first, first))


def test_projector_contracts_are_immutable_and_projection_identity_is_commit_based():
    retry = RetryPolicy(8, 2, 300, 120, 30)
    spec = ProjectorSpec("analytics", "v1", "non_blocking", retry)

    with pytest.raises(FrozenInstanceError):
        spec.version = "v2"  # type: ignore[misc]

    assert projection_event_id("analytics", "commit-42") == sha256(
        b"analytics:commit-42"
    ).hexdigest()
    assert projection_event_id("analytics", "commit-42") != projection_event_id(
        "analytics", "commit-43"
    )


def test_projection_message_accepts_delivery_or_canon_replay_identity():
    message = ProjectionMessage(
        projection_event_id=projection_event_id("analytics", "commit-42"),
        tenant_id="tenant-1",
        project_id="project-1",
        commit_id="commit-42",
        revision_id="revision-42",
        state_version_id="state-42",
        projector_id="analytics",
        barrier_kind="non_blocking",
        event_type="commit.created",
        stream_position=42,
        payload={"key": "value"},
    )

    assert message.outbox_event_id is None
    assert message.delivery_id is None
    assert message.stream_position == 42


def test_projection_executor_exposes_its_resolvable_projector_spec_type():
    assert get_type_hints(ProjectionExecutor)["spec"] is ProjectorSpec
