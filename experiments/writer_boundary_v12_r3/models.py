from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SHA256 = r"^[0-9a-f]{64}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HardOutcome(Strict):
    artifact_status: Literal[
        "present", "provider_failed", "request_rejected", "content_missing", "audit_invalid"
    ]
    mandatory_events_complete: bool | None
    unauthorized_new_character_detected: bool | None
    unauthorized_new_solution_detected: bool | None
    unauthorized_relationship_change_detected: bool | None

    @model_validator(mode="after")
    def coherent(self) -> "HardOutcome":
        values = (
            self.mandatory_events_complete,
            self.unauthorized_new_character_detected,
            self.unauthorized_new_solution_detected,
            self.unauthorized_relationship_change_detected,
        )
        if self.artifact_status == "present" and any(value is None for value in values):
            raise ValueError("present text requires all hard outcomes")
        if self.artifact_status != "present" and any(value is not None for value in values):
            raise ValueError("missing/failed text hard outcomes must be not_evaluable")
        return self


class ExecutionAudit(Strict):
    schema_version: Literal["1.2-r3-audit"] = "1.2-r3-audit"
    reviewer_id: str
    block_id: str
    scene_id: str
    text_id: str
    arm: Literal["A", "B", "C"]
    request_sha256: str = Field(pattern=SHA256)
    content_sha256: str = Field(pattern=SHA256)
    route_identity_accessed: Literal[False] = False
    preference_accessed: Literal[False] = False
    observed_decision: str
    hard: HardOutcome


class PreferenceVote(Strict):
    schema_version: Literal["1.2-r3-vote"] = "1.2-r3-vote"
    reviewer_id: str
    public_block_id: str
    public_a_id: str
    public_c_id: str
    public_a_content_sha256: str = Field(pattern=SHA256)
    public_c_content_sha256: str = Field(pattern=SHA256)
    naturalness: Literal["A", "C", "tie"]
    less_template: Literal["A", "C", "tie"]
    overall_quality: Literal["A", "C", "tie"]
    identity_accessed: Literal[False] = False
    locked: Literal[True] = True


class ProviderReceipt(Strict):
    schema_version: Literal["1.2-r3-provider-receipt"] = "1.2-r3-provider-receipt"
    request_id: str
    expected_envelope_sha256: str = Field(pattern=SHA256)
    consumed_envelope_sha256: str = Field(pattern=SHA256)
    capability_status: Literal["synthetic_only", "verified", "unverified_dependency"]
    retry_count: int = Field(ge=0)
    synthetic: bool

