from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SHA256 = r"^[0-9a-f]{64}$"
PARAGRAPH_ID = r"^P[1-9][0-9]*$"
FAILURE_ID = r"^M[1-9][0-9]*$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HardCheckEvidence(Strict):
    check_id: Literal[
        "mandatory_events",
        "unauthorized_new_character",
        "unauthorized_new_solution",
        "unauthorized_relationship_change",
    ]
    passed: bool
    paragraph_ids: list[str] = Field(min_length=1)
    explanation: str = Field(min_length=1)
    failure_m_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_is_coherent(self) -> "HardCheckEvidence":
        if any(__import__("re").fullmatch(PARAGRAPH_ID, item) is None for item in self.paragraph_ids):
            raise ValueError("paragraph evidence must use P<number>")
        if any(__import__("re").fullmatch(FAILURE_ID, item) is None for item in self.failure_m_ids):
            raise ValueError("failure references must use M<number>")
        if self.passed and self.failure_m_ids:
            raise ValueError("passing check cannot cite failure M ids")
        if not self.passed and not self.failure_m_ids:
            raise ValueError("failed check requires at least one failure M id")
        return self


class NeutralExecutionAudit(Strict):
    schema_version: Literal["1.2-r3.1-neutral-audit"] = "1.2-r3.1-neutral-audit"
    reviewer_id: str = Field(min_length=1)
    public_text_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    content_sha256: str = Field(pattern=SHA256)
    observed_decision: str = Field(min_length=1)
    hard_checks: list[HardCheckEvidence] = Field(min_length=4, max_length=4)
    identity_accessed: Literal[False] = False
    preference_accessed: Literal[False] = False

    @model_validator(mode="after")
    def exactly_one_of_each_check(self) -> "NeutralExecutionAudit":
        expected = {
            "mandatory_events",
            "unauthorized_new_character",
            "unauthorized_new_solution",
            "unauthorized_relationship_change",
        }
        if {item.check_id for item in self.hard_checks} != expected:
            raise ValueError("audit requires exactly the four frozen hard checks")
        return self


Choice = Literal["candidate_1", "candidate_2", "tie"]


class NeutralPreferenceVote(Strict):
    schema_version: Literal["1.2-r3.1-neutral-vote"] = "1.2-r3.1-neutral-vote"
    reviewer_id: str = Field(min_length=1)
    public_block_id: str = Field(min_length=1)
    candidate_1_id: str = Field(min_length=1)
    candidate_2_id: str = Field(min_length=1)
    candidate_1_content_sha256: str = Field(pattern=SHA256)
    candidate_2_content_sha256: str = Field(pattern=SHA256)
    naturalness: Choice
    less_template: Choice
    overall_quality: Choice
    identity_accessed: Literal[False] = False
    locked: Literal[True] = True

    @model_validator(mode="after")
    def candidates_are_distinct(self) -> "NeutralPreferenceVote":
        if self.candidate_1_id == self.candidate_2_id:
            raise ValueError("preference candidates must be distinct")
        return self

