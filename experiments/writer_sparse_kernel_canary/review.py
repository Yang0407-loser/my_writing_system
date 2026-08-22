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
    naturalness: list[str] = Field(min_length=1, max_length=3)
    less_template: list[str] = Field(min_length=1, max_length=3)
    character_credibility: list[str] = Field(min_length=1, max_length=3)
    emotional_residue: list[str] = Field(min_length=1, max_length=3)
    overall_quality: list[str] = Field(min_length=1, max_length=3)
    most_mechanical: list[str] = Field(min_length=1, max_length=3)


class BlockReview(Strict):
    public_block_id: str = Field(pattern=r"^QB-[0-9]{2}$")
    assessments: list[CandidateAssessment] = Field(min_length=3, max_length=3)
    winners: MetricWinners
    confidence: int = Field(ge=1, le=5)
    block_note: str

    @model_validator(mode="after")
    def validate_candidates_and_winners(self) -> "BlockReview":
        candidates = [item.public_text_id for item in self.assessments]
        if len(set(candidates)) != 3:
            raise ValueError("each block must contain three distinct candidates")
        allowed = set(candidates)
        for metric, winners in self.winners.model_dump().items():
            if len(set(winners)) != len(winners) or not set(winners) <= allowed:
                raise ValueError(f"invalid winner set for {metric}")
        return self


class SparseKernelBlindReview(Strict):
    schema_version: Literal["writer-sparse-kernel-blind-review-v0"]
    reviewer_id: str = Field(pattern=r"^SK-BLIND-REVIEWER-[0-9]{2}$")
    scope: ReviewScope
    blocks: list[BlockReview] = Field(min_length=4, max_length=4)
    cross_block_observations: list[str]

    @model_validator(mode="after")
    def block_ids_are_unique(self) -> "SparseKernelBlindReview":
        ids = [block.public_block_id for block in self.blocks]
        if len(set(ids)) != 4:
            raise ValueError("review must contain four distinct blocks")
        return self


def validate_review_against_public(
    review: SparseKernelBlindReview,
    public: dict,
) -> None:
    expected = {
        block["public_block_id"]: {
            candidate["public_text_id"] for candidate in block["candidates"]
        }
        for block in public["blocks"]
    }
    actual = {
        block.public_block_id: {
            assessment.public_text_id for assessment in block.assessments
        }
        for block in review.blocks
    }
    if actual != expected:
        raise ValueError("review block/candidate coverage differs from public material")
