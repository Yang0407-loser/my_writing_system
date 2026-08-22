"""Immutable Candidate, StateTransition and Commit result contracts."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .hashing import sha256_json, sha256_text


class FrozenArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CandidateValidation(FrozenArtifact):
    complete: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class WorldMutationCandidate(FrozenArtifact):
    mutation_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    value: Any
    provenance: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[str, ...] = ()


class CanonicalEventCandidate(FrozenArtifact):
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class SubsectionCandidate(FrozenArtifact):
    schema_version: Literal["subsection-candidate-v0"] = "subsection-candidate-v0"
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    subsection_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    section: int = Field(ge=1)
    subsection: int = Field(ge=1)
    ordinal: int = Field(ge=1)
    title: str
    topic: str
    base_revision_number: int = Field(ge=0)
    base_state_version_id: str = Field(min_length=1)
    draft: str = Field(min_length=1)
    draft_hash: str
    prompt_hash: str = Field(min_length=1)
    validation: CandidateValidation
    handover_candidate: dict[str, Any] | None = None
    world_mutations: tuple[WorldMutationCandidate, ...] = ()
    events: tuple[CanonicalEventCandidate, ...] = ()
    state_frame: dict[str, Any] | None = None
    generation_metadata: dict[str, Any] = Field(default_factory=dict)
    candidate_hash: str

    def hash_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"candidate_hash"})

    @model_validator(mode="after")
    def _validate_recomputable_contract(self) -> "SubsectionCandidate":
        expected_draft_hash = sha256_text(self.draft)
        if self.draft_hash != expected_draft_hash:
            raise ValueError("draft_hash does not match UTF-8 draft content")
        if not self.validation.complete:
            raise ValueError("validation.complete must be true")
        expected_candidate_hash = sha256_json(self.hash_payload())
        if self.candidate_hash != expected_candidate_hash:
            raise ValueError("candidate_hash does not match canonical candidate payload")
        return self

    @classmethod
    def create(cls, **values: Any) -> "SubsectionCandidate":
        payload = dict(values)
        payload.setdefault("schema_version", "subsection-candidate-v0")
        payload["draft_hash"] = sha256_text(payload["draft"])
        payload["candidate_hash"] = sha256_json(payload)
        return cls.model_validate(payload)


class CanonicalStateSnapshot(FrozenArtifact):
    version_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    state_json: dict[str, Any]
    state_hash: str

    @model_validator(mode="after")
    def _validate_state_hash(self) -> "CanonicalStateSnapshot":
        if self.state_hash != sha256_json(self.state_json):
            raise ValueError("state_hash does not match canonical state_json")
        return self

    @classmethod
    def create(cls, **values: Any) -> "CanonicalStateSnapshot":
        payload = dict(values)
        payload["state_hash"] = sha256_json(payload["state_json"])
        return cls.model_validate(payload)


class StateTransitionResult(FrozenArtifact):
    schema_version: Literal["state-transition-result-v0"] = "state-transition-result-v0"
    transition_version: str = Field(min_length=1)
    candidate_hash: str = Field(min_length=1)
    base_state_version_id: str = Field(min_length=1)
    next_state_json: dict[str, Any]
    state_hash: str
    ledger_events: tuple[CanonicalEventCandidate, ...] = ()

    @model_validator(mode="after")
    def _validate_state_hash(self) -> "StateTransitionResult":
        if self.state_hash != sha256_json(self.next_state_json):
            raise ValueError("state_hash does not match canonical next_state_json")
        return self

    @classmethod
    def create(cls, **values: Any) -> "StateTransitionResult":
        payload = dict(values)
        payload.setdefault("schema_version", "state-transition-result-v0")
        payload["state_hash"] = sha256_json(payload["next_state_json"])
        return cls.model_validate(payload)


class PreparedCanonicalCommit(FrozenArtifact):
    candidate: SubsectionCandidate
    state_transition: StateTransitionResult

    @model_validator(mode="after")
    def _validate_alignment(self) -> "PreparedCanonicalCommit":
        if self.candidate.candidate_hash != self.state_transition.candidate_hash:
            raise ValueError("candidate_hash mismatch between candidate and transition")
        if (
            self.candidate.base_state_version_id
            != self.state_transition.base_state_version_id
        ):
            raise ValueError("base_state_version_id mismatch")
        return self


class StateTransitionCompiler(Protocol):
    def compile(
        self,
        *,
        base_state: CanonicalStateSnapshot,
        candidate: SubsectionCandidate,
    ) -> StateTransitionResult: ...


class CanonicalCommitResult(FrozenArtifact):
    schema_version: Literal["canonical-commit-result-v0"] = "canonical-commit-result-v0"
    commit_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    state_version_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    outbox_event_ids: tuple[str, ...] = ()
    idempotency_key: str = Field(min_length=1)
    candidate_hash: str = Field(min_length=1)
    skipped_as_duplicate: bool = False
