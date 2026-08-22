from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from experiments.writer_boundary_v12_r1.models import R1Scene


SHA256 = r"^[0-9a-f]{64}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderConfig(StrictModel):
    provider: Literal["deepseek"]
    model: str
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1)
    json_mode: bool
    thinking: Literal["disabled", "enabled"]
    seed_capability: Literal["unverified_dependency"]


class PilotRule(StrictModel):
    planned_blocks: Literal[12] = 12
    fixed_denominator: Literal[12] = 12
    silent_reruns_allowed: Literal[False] = False
    reserve_runs_allowed: Literal[False] = False
    missing_or_failed_text_retained: Literal[True] = True
    primary_comparison: Literal["A_vs_C_product_regimen"]
    primary_directional_score_min: Literal[8] = 8
    scene_consistency_min_scenes: Literal[3] = 3
    scene_noninferior_blocks_min: Literal[2] = 2
    hard_task_non_degradation_required: Literal[True] = True
    confirmatory_causal_claims_allowed: Literal[False] = False
    secondary_controls_expansion: Literal[False] = False
    tie_score: Literal[0.5] = 0.5


class R2Protocol(StrictModel):
    schema_version: Literal["1.2-r2"] = "1.2-r2"
    experiment_id: Literal["writer-boundary-v1-2-r2"]
    enabled: Literal[False] = False
    generation_authorized: Literal[False] = False
    arms: list[Literal["A", "B", "C"]]
    repeats_per_scene: Literal[3] = 3
    base_r1_protocol_sha256: str = Field(pattern=SHA256)
    provider_config: ProviderConfig
    pilot_rule: PilotRule
    estimands: dict[Literal["primary", "secondary", "diagnostic"], str]
    scenes: list[R1Scene] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def balanced(self) -> "R2Protocol":
        if self.arms != ["A", "B", "C"]:
            raise ValueError("arms must be A, B, C")
        if len({scene.scene_id for scene in self.scenes}) != 4:
            raise ValueError("four unique scenes required")
        return self


class AssignmentTicket(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.2-r2-assignment"] = "1.2-r2-assignment"
    assignment_id: str = Field(pattern=r"^ASSIGN-\d{2}$")
    block_id: str = Field(pattern=r"^BLOCK-\d{2}$")
    scene_id: str = Field(pattern=r"^SC(9|10|11|12)$")
    decision_id: str
    selected_value: str
    selected_definition: str
    selected_summary: str
    matrix_sha256: str = Field(pattern=SHA256)
    locked: Literal[True] = True


class RequestEnvelope(StrictModel):
    schema_version: Literal["1.2-r2-request"] = "1.2-r2-request"
    experiment_id: Literal["writer-boundary-v1-2-r2"]
    block_id: str
    text_id: str
    arm: Literal["A", "B", "C"]
    request_nonce: str
    provider_config: ProviderConfig
    messages: list[dict[str, Any]]
    protocol_sha256: str = Field(pattern=SHA256)
    assignment_sha256: str | None


class LedgerState(StrEnum):
    DESIGN_LOCKED = "DESIGN_LOCKED"
    ASSIGNMENT_LEDGER_LOCKED = "ASSIGNMENT_LEDGER_LOCKED"
    REQUEST_LEDGER_LOCKED = "REQUEST_LEDGER_LOCKED"
    ALL_TEXTS_LOCKED = "ALL_TEXTS_LOCKED"
    EXECUTION_AUDITS_LOCKED = "EXECUTION_AUDITS_LOCKED"
    BLIND_JOIN_LOCKED = "BLIND_JOIN_LOCKED"
    PREFERENCE_VOTES_LOCKED = "PREFERENCE_VOTES_LOCKED"
    IDENTITY_UNBLINDED = "IDENTITY_UNBLINDED"
    AGGREGATED = "AGGREGATED"


class LedgerRecord(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.2-r2-ledger"] = "1.2-r2-ledger"
    experiment_id: Literal["writer-boundary-v1-2-r2"]
    sequence: int = Field(ge=0)
    state: LedgerState
    actor_role: Literal[
        "designer",
        "assignment_builder",
        "request_builder",
        "text_ingestor",
        "execution_auditor",
        "blind_pack_builder",
        "preference_reviewer",
        "identity_custodian",
        "aggregator",
    ]
    artifact_hashes: dict[str, str]
    previous_chain_head: str | None
    record_sha256: str = Field(pattern=SHA256)


class PrivateJoinRow(StrictModel):
    block_id: str
    public_block_id: str
    private_text_id: str
    arm: Literal["A", "B", "C"]
    public_text_id: str
    public_position: int = Field(ge=1, le=3)
    content_sha256: str | None = Field(default=None, pattern=SHA256)


class ArmHardOutcome(StrictModel):
    text_present: bool
    mandatory_events_complete: bool
    unauthorized_new_character_detected: bool
    unauthorized_new_solution_detected: bool
    unauthorized_relationship_change_detected: bool


class PilotBlockOutcome(StrictModel):
    block_id: str = Field(pattern=r"^BLOCK-\d{2}$")
    scene_id: str = Field(pattern=r"^SC(9|10|11|12)$")
    naturalness: Literal["A", "C", "tie"]
    less_template: Literal["A", "C", "tie"]
    overall_quality: Literal["A", "C", "tie"]
    arm_a_hard: ArmHardOutcome
    arm_c_hard: ArmHardOutcome

    @model_validator(mode="after")
    def missing_text_preference_is_fixed(self) -> "PilotBlockOutcome":
        expected = None
        if not self.arm_a_hard.text_present and self.arm_c_hard.text_present:
            expected = "C"
        elif self.arm_a_hard.text_present and not self.arm_c_hard.text_present:
            expected = "A"
        elif not self.arm_a_hard.text_present and not self.arm_c_hard.text_present:
            expected = "tie"
        if expected and any(
            value != expected
            for value in (self.naturalness, self.less_template, self.overall_quality)
        ):
            raise ValueError("missing-text preferences must use fixed automatic outcome")
        return self
