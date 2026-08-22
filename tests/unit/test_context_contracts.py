import json

import pytest
from pydantic import ValidationError

from app.context_contracts import (
    ContextHardness,
    ContextItem,
    ContextKind,
    EventEdge,
    EventRelationType,
    StoryChunkMetadata,
    serialize_chroma_metadata,
)


def test_context_item_rejects_invalid_section_and_priority():
    with pytest.raises(ValidationError):
        ContextItem(
            id="ctx-1",
            kind=ContextKind.RULE,
            content="必须保留原文",
            source_id="rule-1",
            source_version=1,
            project_id="project-1",
            section=-1,
            priority=11,
            hardness=ContextHardness.HARD,
            reason="全局硬约束",
        )


def test_story_chunk_metadata_serializes_lists_stably():
    metadata = StoryChunkMetadata(
        project_id="project-1",
        task_id="task-1",
        section=3,
        characters=["林晚", "周野"],
        content_hash="abc123",
        created_at="2026-07-17T12:00:00Z",
    ).to_chroma()

    assert metadata["characters"] == '["林晚","周野"]'
    assert json.loads(metadata["event_ids"]) == []
    assert metadata["section"] == 3


def test_event_edge_requires_direction_and_evidence():
    edge = EventEdge(
        from_event_id="event-1",
        to_event_id="event-2",
        relation_type=EventRelationType.CAUSES,
        confidence=0.8,
        evidence_source_ids=["chunk-1"],
    )
    assert edge.relation_type is EventRelationType.CAUSES

    with pytest.raises(ValidationError):
        EventEdge(
            from_event_id="event-1",
            to_event_id="event-1",
            relation_type=EventRelationType.FOLLOWS,
            confidence=0.5,
            evidence_source_ids=["chunk-1"],
        )


def test_serialize_chroma_metadata_drops_none_and_normalizes_complex_values():
    metadata = serialize_chroma_metadata(
        {"task_id": "task-1", "characters": ["周野"], "optional": None, "version": 2}
    )
    assert metadata == {
        "task_id": "task-1",
        "characters": '["周野"]',
        "version": 2,
    }
