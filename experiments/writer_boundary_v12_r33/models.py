from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SHA256 = r"^[0-9a-f]{64}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MandatoryEventEvidence(Strict):
    m_id: str
    passed: bool
    paragraph_ids: list[str] = Field(min_length=1)
    explanation: str

    @field_validator("m_id")
    @classmethod
    def valid_m(cls, value: str) -> str:
        if not re.fullmatch(r"M[1-9][0-9]*", value):
            raise ValueError("m_id must be M<number>")
        return value

    @field_validator("paragraph_ids")
    @classmethod
    def unique_paragraphs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not re.fullmatch(r"P[1-9][0-9]*", item) for item in value):
            raise ValueError("paragraph ids must be unique P<number> values")
        return value

    @field_validator("explanation")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("explanation cannot be blank")
        return value


class ViolationEvidence(Strict):
    check_id: Literal[
        "unauthorized_new_character",
        "unauthorized_new_solution",
        "unauthorized_relationship_change",
    ]
    detected: bool
    paragraph_ids: list[str] = Field(min_length=1)
    explanation: str
    f_ids: list[str]

    @field_validator("paragraph_ids")
    @classmethod
    def unique_paragraphs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not re.fullmatch(r"P[1-9][0-9]*", item) for item in value):
            raise ValueError("paragraph ids must be unique P<number> values")
        return value

    @field_validator("f_ids")
    @classmethod
    def unique_f_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not re.fullmatch(r"F[1-9][0-9]*", item) for item in value):
            raise ValueError("f_ids must be unique F<number> values")
        return value

    @field_validator("explanation")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("explanation cannot be blank")
        return value

    @model_validator(mode="after")
    def detected_requires_f(self) -> "ViolationEvidence":
        if self.detected and not self.f_ids:
            raise ValueError("detected violation requires F ids")
        if not self.detected and self.f_ids:
            raise ValueError("non-detected violation cannot cite F ids")
        return self


class ExecutionAudit(Strict):
    schema_version: Literal["1.2-r3.3-execution-audit"]
    reviewer_id: str
    dispatch_sha256: str = Field(pattern=SHA256)
    public_text_id: str
    scene_id: str
    content_sha256: str = Field(pattern=SHA256)
    observed_decision: str
    mandatory_events: list[MandatoryEventEvidence]
    violations: list[ViolationEvidence]
    identity_accessed: Literal[False]
    preference_accessed: Literal[False]
    other_reviews_accessed: Literal[False]
    private_material_accessed: Literal[False]
    public_material_only: Literal[True]

    @model_validator(mode="after")
    def violation_checks_exact(self) -> "ExecutionAudit":
        expected = {
            "unauthorized_new_character",
            "unauthorized_new_solution",
            "unauthorized_relationship_change",
        }
        if len(self.violations) != 3 or {item.check_id for item in self.violations} != expected:
            raise ValueError("three violation checks are required exactly once")
        if len(self.mandatory_events) != len({item.m_id for item in self.mandatory_events}):
            raise ValueError("mandatory event evidence cannot be duplicated")
        return self


Choice = Literal["candidate_1", "candidate_2", "tie"]


class PreferenceVote(Strict):
    schema_version: Literal["1.2-r3.3-preference-vote"]
    reviewer_id: str
    dispatch_sha256: str = Field(pattern=SHA256)
    public_block_id: str
    candidate_1_id: str
    candidate_2_id: str
    candidate_1_content_sha256: str = Field(pattern=SHA256)
    candidate_2_content_sha256: str = Field(pattern=SHA256)
    naturalness: Choice
    less_template: Choice
    overall_quality: Choice
    identity_accessed: Literal[False]
    other_reviews_accessed: Literal[False]
    private_material_accessed: Literal[False]
    execution_audits_accessed: Literal[False]
    public_material_only: Literal[True]
    locked: Literal[True]

    @model_validator(mode="after")
    def candidates_distinct(self) -> "PreferenceVote":
        if self.candidate_1_id == self.candidate_2_id:
            raise ValueError("candidates must be distinct")
        return self

