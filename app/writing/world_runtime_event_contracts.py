"""Typed subsection event inputs shared by World Runtime fixtures and compiler."""

from __future__ import annotations

from pydantic import Field, model_validator

from .world_runtime_contracts import (
    FrozenRuntimeModel,
    ProvenanceRef,
    canonical_hash,
)


WORLD_RUNTIME_EVENT_CONTRACT_VERSION = "world-runtime-event-contract-wr0f-v1"


class EventRuntimeBinding(FrozenRuntimeModel):
    fact_ids: tuple[str, ...] = Field(min_length=1)
    semantic_domains: tuple[str, ...] = Field(min_length=1)
    lifecycle_id: str | None = None
    lifecycle_state_fact_id: str | None = None
    required_transition_ids: tuple[str, ...] = ()
    schema_version: str = WORLD_RUNTIME_EVENT_CONTRACT_VERSION

    @model_validator(mode="after")
    def validate_lifecycle_binding(self):
        lifecycle_fields = (self.lifecycle_id, self.lifecycle_state_fact_id)
        if self.required_transition_ids and not all(lifecycle_fields):
            raise ValueError("required transitions need lifecycle and state fact")
        if any(lifecycle_fields) and not all(lifecycle_fields):
            raise ValueError("lifecycle binding requires lifecycle and state fact")
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("runtime binding fact IDs must be unique")
        if len(self.semantic_domains) != len(set(self.semantic_domains)):
            raise ValueError("runtime binding semantic domains must be unique")
        return self


class EventRequirement(FrozenRuntimeModel):
    event_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool = True
    runtime_binding: EventRuntimeBinding
    schema_version: str = WORLD_RUNTIME_EVENT_CONTRACT_VERSION


class SubsectionEventContract(FrozenRuntimeModel):
    contract_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    section: int = Field(ge=1)
    subsection: int = Field(ge=1)
    requirements: tuple[EventRequirement, ...] = Field(min_length=1)
    provenance: ProvenanceRef
    schema_version: str = WORLD_RUNTIME_EVENT_CONTRACT_VERSION

    @model_validator(mode="after")
    def reject_duplicate_events(self):
        event_ids = [item.event_id for item in self.requirements]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event contract requirement IDs must be unique")
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)
