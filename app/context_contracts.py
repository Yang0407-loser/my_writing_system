"""Contracts shared by context retrieval, auditing, and future Context Broker work.

These models intentionally do not replace the current persistence layer.  They
define the boundary that later phases can migrate to without making Writer or
ChromaDB own another copy of story state.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ContextKind(str, Enum):
    RULE = "rule"
    CHARACTER_PROFILE = "character_profile"
    CHARACTER_STATE = "character_state"
    RELATIONSHIP = "relationship"
    EVENT = "event"
    WORLD_FACT = "world_fact"
    FORESHADOWING = "foreshadowing"
    HANDOVER = "handover"
    STORY_CHUNK = "story_chunk"
    STYLE = "style"


class ContextHardness(str, Enum):
    HARD = "hard"
    SOFT = "soft"
    EVIDENCE = "evidence"


class ContextItem(BaseModel):
    """A traceable unit that may be selected for Writer context."""

    id: str = Field(min_length=1)
    kind: ContextKind
    content: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version: str | int
    project_id: str = Field(min_length=1)
    section: int | None = Field(default=None, ge=0)
    subsection: int | None = Field(default=None, ge=0)
    characters: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    priority: int = Field(default=5, ge=1, le=10)
    hardness: ContextHardness = ContextHardness.SOFT
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    token_estimate: int = Field(default=0, ge=0)
    reason: str = Field(min_length=1)


class StoryChunkMetadata(BaseModel):
    """Canonical StoryChunk metadata with a Chroma-safe serializer."""

    project_id: str = ""
    task_id: str = Field(min_length=1)
    section: int = Field(ge=0)
    subsection: int = Field(default=0, ge=0)
    title: str = ""
    characters: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    foreshadowing_ids: list[str] = Field(default_factory=list)
    event_type: str = ""
    timeline_position: str = ""
    content_hash: str = Field(min_length=1)
    source_version: str | int = 1
    created_at: str = Field(min_length=1)

    def to_chroma(self) -> dict[str, str | int | float | bool]:
        """Serialize complex fields deterministically for Chroma metadata."""

        result: dict[str, str | int | float | bool] = {}
        for key, value in self.model_dump().items():
            if isinstance(value, (list, dict)):
                result[key] = json.dumps(
                    value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            else:
                result[key] = value
        return result


class EventSource(str, Enum):
    PLANNED = "planned"
    EXTRACTED = "extracted"
    USER = "user"


class ActualEventStatus(str, Enum):
    PLANNED = "planned"
    OCCURRED = "occurred"
    CONTRADICTED = "contradicted"
    CANCELLED = "cancelled"


class EventRelationType(str, Enum):
    CAUSES = "causes"
    ENABLES = "enables"
    BLOCKS = "blocks"
    RESOLVES = "resolves"
    FORESHADOWS = "foreshadows"
    MOTIVATES = "motivates"
    FOLLOWS = "follows"


class NarrativeEventContract(BaseModel):
    """Target event contract; persistence migration is deliberately deferred."""

    event_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    section: int = Field(ge=0)
    subsection: int = Field(default=0, ge=0)
    source: EventSource
    actual_status: ActualEventStatus
    participants: list[str] = Field(default_factory=list)
    location_id: str = ""
    source_chunk_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class EventEdge(BaseModel):
    """A directed, typed event edge backed by explicit evidence."""

    from_event_id: str = Field(min_length=1)
    to_event_id: str = Field(min_length=1)
    relation_type: EventRelationType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_source_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_self_loop(self) -> "EventEdge":
        if self.from_event_id == self.to_event_id:
            raise ValueError("event edge cannot point to itself")
        return self


def serialize_chroma_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """Normalize arbitrary metadata without silently passing unsupported values."""

    normalized: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            normalized[str(key)] = value
        elif isinstance(value, (list, dict, tuple, set)):
            stable_value = sorted(value) if isinstance(value, set) else value
            normalized[str(key)] = json.dumps(
                stable_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        else:
            normalized[str(key)] = str(value)
    return normalized
