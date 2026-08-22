from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from experiments.writer_boundary_v12_preflight.models import (
    SolutionBoundaryPolicy,
    SolutionCandidate,
)


SHA256_PATTERN = r"^[0-9a-f]{64}$"
PARAGRAPH_PATTERN = r"^P\d+$"
EVENT_PATTERN = r"^M\d+$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DecisionOption(StrictModel):
    value: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    selected_summary: str = Field(min_length=1)
    allowed_implementation_details: list[str] = Field(min_length=2)


class SceneDecisionContract(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.2-r1"] = "1.2-r1"
    scene_id: str = Field(pattern=r"^SC(9|10|11|12)$")
    decision_id: str = Field(min_length=1)
    choose_exactly: Literal[1] = 1
    allowed_values: list[DecisionOption] = Field(min_length=2, max_length=2)
    solution_boundary_policy: SolutionBoundaryPolicy

    @model_validator(mode="after")
    def distinct_options(self) -> "SceneDecisionContract":
        if len({item.value for item in self.allowed_values}) != 2:
            raise ValueError("two distinct decision values are required")
        return self


class StyleSignature(StrictModel):
    narrative_distance: str
    sentence_rhythm: str
    paragraph_rhythm: str
    dialogue_function: str
    emotional_mediation: str
    diction_register: str


class R1Scene(StrictModel):
    schema_version: Literal["1.2-r1"] = "1.2-r1"
    scene_id: str = Field(pattern=r"^SC(9|10|11|12)$")
    topology: Literal[
        "scarce_resource_allocation",
        "reversible_action_ordering",
        "authority_bounded_response",
        "uncertainty_preservation",
    ]
    scene: str = Field(min_length=1)
    characters: dict[str, str]
    world_facts: list[str] = Field(min_length=3)
    primary_obligation: str = Field(min_length=1)
    decision_shape: str = Field(min_length=1)
    long_term_problem: str = Field(min_length=1)
    mandatory_events: list[str] = Field(min_length=5, max_length=7)
    forbidden_events: list[str] = Field(min_length=6)
    style_signature: StyleSignature
    target_chars: int = Field(ge=850, le=1300)
    decision_contract: SceneDecisionContract

    @model_validator(mode="after")
    def validate_scene(self) -> "R1Scene":
        if len(self.characters) != 2:
            raise ValueError("each scene requires exactly two named characters")
        if self.decision_contract.scene_id != self.scene_id:
            raise ValueError("scene and contract IDs must match")
        ids = [item.split(maxsplit=1)[0] for item in self.mandatory_events]
        if ids != [f"M{i}" for i in range(1, len(ids) + 1)]:
            raise ValueError("mandatory event IDs must be consecutive and ordered")
        return self


class FrozenModelConfig(StrictModel):
    provider: Literal["deepseek"]
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1)
    seed_supported: bool
    json_mode: bool
    thinking: Literal["disabled", "enabled"]


class Estimand(StrictModel):
    estimand_id: Literal[
        "A_vs_B_decision_lock_effect",
        "B_vs_C_contract_exposure_effect",
        "A_vs_C_product_bundle_effect",
    ]
    role: Literal["primary", "secondary", "descriptive"]
    comparison: Literal["A_vs_B", "B_vs_C", "A_vs_C"]
    population: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)


class TripletValidityPolicy(StrictModel):
    planned_triplets: Literal[12] = 12
    minimum_valid_triplets: Literal[9] = 9
    minimum_valid_per_scene: Literal[2] = 2
    invalid_anchor_values: list[Literal["unclear", "other"]]
    silent_rerun_allowed: Literal[False] = False
    reserve_seed_per_scene: Literal[1] = 1
    fail_closed_when_below_threshold: Literal[True] = True
    invalid_triplets_reported_in_full: Literal[True] = True


class R1Protocol(StrictModel):
    schema_version: Literal["1.2-r1"] = "1.2-r1"
    experiment_id: Literal["writer-boundary-v1-2-r1"]
    enabled: Literal[False] = False
    generation_authorized: Literal[False] = False
    arms: list[Literal["A", "B", "C"]]
    repeats_per_scene: Literal[3] = 3
    reserve_repeats_per_scene: Literal[1] = 1
    base_seed: int
    model_config_prose: FrozenModelConfig
    estimands: list[Estimand] = Field(min_length=3, max_length=3)
    triplet_validity: TripletValidityPolicy
    scenes: list[R1Scene] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def balanced_protocol(self) -> "R1Protocol":
        if self.arms != ["A", "B", "C"]:
            raise ValueError("arms must be ordered A, B, C")
        if len({scene.scene_id for scene in self.scenes}) != 4:
            raise ValueError("four unique scenes required")
        if len({scene.topology for scene in self.scenes}) != 4:
            raise ValueError("four unique action topologies required")
        roles = [item.role for item in self.estimands]
        if roles != ["primary", "secondary", "descriptive"]:
            raise ValueError("estimands must be primary, secondary, descriptive")
        return self


class ParagraphEvidence(StrictModel):
    paragraph_ids: list[str] = Field(min_length=1)
    description: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_paragraph_ids(self) -> "ParagraphEvidence":
        import re

        if any(re.fullmatch(PARAGRAPH_PATTERN, value) is None for value in self.paragraph_ids):
            raise ValueError("paragraph IDs must use P plus digits")
        return self


class ExplicitHardChecks(StrictModel):
    mandatory_events_complete: bool
    mandatory_events_evidence: ParagraphEvidence
    failed_event_ids: list[str]
    unauthorized_new_character_detected: bool
    unauthorized_new_character_evidence: ParagraphEvidence
    unauthorized_new_solution_detected: bool
    unauthorized_new_solution_evidence: ParagraphEvidence
    unauthorized_solution_candidates: list[SolutionCandidate]
    unauthorized_relationship_change_detected: bool
    unauthorized_relationship_change_evidence: ParagraphEvidence
    ending_remains_temporary: bool
    ending_evidence: ParagraphEvidence
    boundary_contract_satisfied: bool
    boundary_contract_evidence: ParagraphEvidence

    @model_validator(mode="after")
    def status_consistency(self) -> "ExplicitHardChecks":
        import re

        if self.mandatory_events_complete and self.failed_event_ids:
            raise ValueError("complete mandatory events cannot list failures")
        if not self.mandatory_events_complete and not self.failed_event_ids:
            raise ValueError("failed mandatory events require event IDs")
        if any(re.fullmatch(EVENT_PATTERN, value) is None for value in self.failed_event_ids):
            raise ValueError("failed event IDs must use M plus digits")
        confirmed = any(
            item.classification == "confirmed_new_solution"
            for item in self.unauthorized_solution_candidates
        )
        if self.unauthorized_new_solution_detected != confirmed:
            raise ValueError("new-solution status must match confirmed candidates")
        return self


class ObservedDecision(StrictModel):
    value: str = Field(min_length=1)
    evidence: ParagraphEvidence


class LockedExecutionAudit(StrictModel):
    reviewer_id: str = Field(min_length=1)
    audited_at: datetime
    text_sha256: str = Field(pattern=SHA256_PATTERN)
    route_identity_accessed: Literal[False] = False
    preference_votes_accessed: Literal[False] = False
    locked: Literal[True] = True
    observed_decision: ObservedDecision


class R1PostWriteReview(StrictModel):
    schema_version: Literal["1.2-r1-review"] = "1.2-r1-review"
    text_id: str = Field(pattern=r"^T\d{3}$")
    scene_id: str = Field(pattern=r"^SC(9|10|11|12)$")
    paragraph_count: int = Field(ge=1)
    hard_checks: ExplicitHardChecks
    execution_audit: LockedExecutionAudit


class DecisionTicket(StrictModel):
    schema_version: Literal["1.2-r1-ticket"] = "1.2-r1-ticket"
    triplet_id: str = Field(pattern=r"^TRIPLET-\d{2}$")
    scene_id: str = Field(pattern=r"^SC(9|10|11|12)$")
    decision_id: str = Field(min_length=1)
    selected_value: str = Field(min_length=1)
    selected_definition: str = Field(min_length=1)
    selected_summary: str = Field(min_length=1)
    source_a_text_sha256: str = Field(pattern=SHA256_PATTERN)
    source_a_audit_sha256: str = Field(pattern=SHA256_PATTERN)
    locked: Literal[True] = True


class WorkflowState(StrEnum):
    DESIGN_LOCKED = "DESIGN_LOCKED"
    A_TEXT_LOCKED = "A_TEXT_LOCKED"
    A_EXECUTION_AUDIT_LOCKED = "A_EXECUTION_AUDIT_LOCKED"
    DECISION_TICKET_LOCKED = "DECISION_TICKET_LOCKED"
    B_C_TEXTS_LOCKED = "B_C_TEXTS_LOCKED"
    ALL_HARD_AUDITS_LOCKED = "ALL_HARD_AUDITS_LOCKED"
    BLIND_PUBLIC_PACK_LOCKED = "BLIND_PUBLIC_PACK_LOCKED"
    PREFERENCE_VOTES_LOCKED = "PREFERENCE_VOTES_LOCKED"
    IDENTITY_UNBLINDED = "IDENTITY_UNBLINDED"
    AGGREGATED = "AGGREGATED"


class StateRecord(StrictModel):
    schema_version: Literal["1.2-r1-state"] = "1.2-r1-state"
    triplet_id: str = Field(pattern=r"^TRIPLET-\d{2}$")
    state: WorkflowState
    actor_id: str = Field(min_length=1)
    input_hashes: list[str]
    output_hash: str = Field(pattern=SHA256_PATTERN)
    previous_record_hash: str | None
    locked: Literal[True] = True
    mock_only: Literal[True] = True

