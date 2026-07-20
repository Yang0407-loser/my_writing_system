"""Typed artifacts passed through the behavior-preserving writing pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)


class SubsectionInput(FrozenArtifact):
    task_id: str
    section: int
    subsection: int
    outline_target: str
    target_words: int
    generation_settings: dict[str, Any] = Field(default_factory=dict)
    prepared_context_fields: dict[str, Any] = Field(default_factory=dict)
    source_manifest: list[dict[str, Any]] = Field(default_factory=list)


class PromptArtifact(FrozenArtifact):
    messages: list[dict[str, str]]
    messages_hash: str
    content_hash: str
    estimated_tokens: int
    token_by_source: dict[str, int] = Field(default_factory=dict)
    source_manifest: list[dict[str, Any]] = Field(default_factory=list)
    prompt_version: str


class GenerationArtifact(FrozenArtifact):
    raw_output: str
    draft: str
    generation_attempts: list[dict[str, Any]] = Field(default_factory=list)
    finish_reason: str
    latency_ms: float
    output_hash: str


class CommitArtifact(FrozenArtifact):
    idempotency_key: str
    committed_fields: list[str] = Field(default_factory=list)
    source_hash: str
    output_hash: str
    checkpoint_version: str
    warnings: list[str] = Field(default_factory=list)
    rollback_information: dict[str, Any] = Field(default_factory=dict)
    skipped_as_duplicate: bool = False


class SubsectionPipelineArtifact(FrozenArtifact):
    trace_id: str
    phase: str
    prepared: SubsectionInput
    prompt: PromptArtifact | None = None
    generation: GenerationArtifact | None = None
    validation: dict[str, Any] | None = None
    commit: CommitArtifact | None = None
    phase_history: list[str] = Field(default_factory=list)


EpistemicStatus = Literal["confirmed", "planned", "unknown", "conflicted"]


class SourceEvidence(FrozenArtifact):
    evidence_id: str
    source_id: str
    source_type: str
    text_hash: str
    section: int | None = None
    subsection: int | None = None
    span_start: int | None = None
    span_end: int | None = None
    excerpt: str = ""


class StateAssertion(FrozenArtifact):
    assertion_id: str
    subject: str
    predicate: str
    value: str
    status: EpistemicStatus
    evidence_ids: list[str] = Field(min_length=1)


class StoryStateSnapshot(FrozenArtifact):
    task_id: str
    section: int
    subsection: int
    evidence: list[SourceEvidence] = Field(default_factory=list)
    assertions: list[StateAssertion] = Field(default_factory=list)
    open_loops: list[StateAssertion] = Field(default_factory=list)
    hard_constraints: list[StateAssertion] = Field(default_factory=list)
    source_hash: str


class SceneSpec(FrozenArtifact):
    scene_id: str
    task_id: str
    section: int
    subsection: int
    confirmed_state: list[StateAssertion] = Field(default_factory=list)
    planned_events: list[StateAssertion] = Field(default_factory=list)
    open_loops: list[StateAssertion] = Field(default_factory=list)
    hard_constraints: list[StateAssertion] = Field(default_factory=list)
    forbidden_inferences: list[StateAssertion] = Field(default_factory=list)
    unknowns_and_conflicts: list[StateAssertion] = Field(default_factory=list)
    evidence: list[SourceEvidence] = Field(default_factory=list)
    source_hash: str
    spec_hash: str
    estimated_tokens: int
    schema_version: str = "phase4r-r2-v1"
