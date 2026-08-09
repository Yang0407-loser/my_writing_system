from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SHA256 = r"^[0-9a-f]{64}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Strict):
    check_id: Literal[
        "mandatory_events",
        "unauthorized_new_character",
        "unauthorized_new_solution",
        "unauthorized_relationship_change",
    ]
    passed: bool
    paragraph_ids: list[str] = Field(min_length=1)
    explanation: str
    failure_m_ids: list[str] = Field(default_factory=list)

    @field_validator("paragraph_ids")
    @classmethod
    def paragraph_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not re.fullmatch(r"P[1-9][0-9]*", item) for item in value):
            raise ValueError("paragraph ids must be unique P<number> values")
        return value

    @field_validator("failure_m_ids")
    @classmethod
    def m_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not re.fullmatch(r"M[1-9][0-9]*", item) for item in value):
            raise ValueError("failure ids must be unique M<number> values")
        return value

    @field_validator("explanation")
    @classmethod
    def explanation_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("explanation cannot be blank")
        return value

    @model_validator(mode="after")
    def failure_is_explained(self) -> "Evidence":
        if self.passed and self.failure_m_ids:
            raise ValueError("passing check cannot cite failed M ids")
        if not self.passed and not self.failure_m_ids:
            raise ValueError("failed check requires M evidence")
        return self


class NeutralAudit(Strict):
    schema_version: Literal["1.2-r3.2-neutral-audit"] = "1.2-r3.2-neutral-audit"
    reviewer_id: str
    public_text_id: str
    scene_id: str
    content_sha256: str = Field(pattern=SHA256)
    artifact_status: Literal["present", "missing", "provider_failed"] = "present"
    observed_decision: str | None
    hard_checks: list[Evidence]
    identity_accessed: Literal[False] = False
    preference_accessed: Literal[False] = False
    other_reviews_accessed: Literal[False] = False
    private_material_accessed: Literal[False] = False
    public_material_only: Literal[True] = True

    @model_validator(mode="after")
    def audit_shape(self) -> "NeutralAudit":
        expected = {
            "mandatory_events",
            "unauthorized_new_character",
            "unauthorized_new_solution",
            "unauthorized_relationship_change",
        }
        if self.artifact_status == "present":
            if self.observed_decision is None or {item.check_id for item in self.hard_checks} != expected:
                raise ValueError("present text requires four checks and a decision")
        elif self.observed_decision is not None or self.hard_checks:
            raise ValueError("missing text is not evaluable")
        return self


Choice = Literal["candidate_1", "candidate_2", "tie"]


class NeutralVote(Strict):
    schema_version: Literal["1.2-r3.2-neutral-vote"] = "1.2-r3.2-neutral-vote"
    reviewer_id: str
    public_block_id: str
    candidate_1_id: str
    candidate_2_id: str
    candidate_1_content_sha256: str = Field(pattern=SHA256)
    candidate_2_content_sha256: str = Field(pattern=SHA256)
    naturalness: Choice
    less_template: Choice
    overall_quality: Choice
    identity_accessed: Literal[False] = False
    other_reviews_accessed: Literal[False] = False
    private_material_accessed: Literal[False] = False
    execution_audits_accessed: Literal[False] = False
    public_material_only: Literal[True] = True
    locked: Literal[True] = True

    @model_validator(mode="after")
    def distinct_candidates(self) -> "NeutralVote":
        if self.candidate_1_id == self.candidate_2_id:
            raise ValueError("candidates must be distinct")
        return self

