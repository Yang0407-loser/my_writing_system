from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewScope(Strict):
    independent_fresh_conversation: Literal[True]
    blind_key_accessed: Literal[False]
    other_reviews_accessed: Literal[False]
    private_material_accessed: Literal[False]
    prompts_or_arm_identity_accessed: Literal[False]
    public_material_only: Literal[True]
    external_or_story_model_called: Literal[False]


class ParagraphEvidence(Strict):
    paragraph_id: str = Field(pattern=r"^P[0-9]{2}$")
    explanation: str = Field(min_length=1)


class CandidateAssessment(Strict):
    public_text_id: str = Field(pattern=r"^Q[0-9]{2}$")
    hard_task_complete: bool
    unauthorized_event_detected: bool
    hard_evidence: list[ParagraphEvidence] = Field(min_length=1)
    naturalness: int = Field(ge=1, le=5)
    less_template: int = Field(ge=1, le=5)
    character_credibility: int = Field(ge=1, le=5)
    emotional_residue: int = Field(ge=1, le=5)
    overall_quality: int = Field(ge=1, le=5)
    mechanicalness: int = Field(ge=1, le=5)
    quality_evidence: list[ParagraphEvidence] = Field(min_length=1)


class MetricWinners(Strict):
    naturalness: list[str] = Field(min_length=1, max_length=2)
    less_template: list[str] = Field(min_length=1, max_length=2)
    character_credibility: list[str] = Field(min_length=1, max_length=2)
    emotional_residue: list[str] = Field(min_length=1, max_length=2)
    overall_quality: list[str] = Field(min_length=1, max_length=2)
    most_mechanical: list[str] = Field(min_length=1, max_length=2)


class BlockReview(Strict):
    public_block_id: str = Field(pattern=r"^QB-[0-9]{2}$")
    assessments: list[CandidateAssessment] = Field(min_length=2, max_length=2)
    winners: MetricWinners
    confidence: int = Field(ge=1, le=5)
    block_note: str

    @model_validator(mode="after")
    def validate_ids(self) -> "BlockReview":
        candidates = [item.public_text_id for item in self.assessments]
        if len(set(candidates)) != 2:
            raise ValueError("block must contain two distinct candidates")
        allowed = set(candidates)
        for metric, winners in self.winners.model_dump().items():
            if len(set(winners)) != len(winners) or not set(winners) <= allowed:
                raise ValueError(f"invalid winners for {metric}")
        return self


class RealizationPolicyBlindReview(Strict):
    schema_version: Literal["realization-policy-blind-review-v1"]
    reviewer_id: str = Field(pattern=r"^RP-BLIND-REVIEWER-[0-9]{2}$")
    scope: ReviewScope
    blocks: list[BlockReview] = Field(min_length=8, max_length=8)
    cross_block_observations: list[str]

    @model_validator(mode="after")
    def unique_blocks(self) -> "RealizationPolicyBlindReview":
        if len({item.public_block_id for item in self.blocks}) != 8:
            raise ValueError("review must cover eight distinct blocks")
        return self


def validate_against_public(
    review: RealizationPolicyBlindReview,
    public: dict,
) -> None:
    expected = {
        block["public_block_id"]: {
            item["public_text_id"] for item in block["candidates"]
        }
        for block in public["blocks"]
    }
    actual = {
        block.public_block_id: {
            item.public_text_id for item in block.assessments
        }
        for block in review.blocks
    }
    if actual != expected:
        raise ValueError("review coverage differs from public material")
