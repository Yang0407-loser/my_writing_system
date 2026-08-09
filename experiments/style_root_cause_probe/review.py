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
    mode_classification: Literal["traditional_literary", "commercial_web_fiction", "generic_or_unclear"]
    hard_task_complete: bool
    unauthorized_event_detected: bool
    literary_intentionality: int = Field(ge=1, le=5)
    commercial_momentum: int = Field(ge=1, le=5)
    narrative_intentionality: int = Field(ge=1, le=5)
    redundant_explanation: int = Field(ge=1, le=5)
    formulaic_expression: int = Field(ge=1, le=5)
    prompt_structure_leak: int = Field(ge=1, le=5)
    character_motivation_credibility: int = Field(ge=1, le=5)
    overall_ai_taste: int = Field(ge=1, le=5)
    evidence: list[ParagraphEvidence] = Field(min_length=1)


class BlockReview(Strict):
    public_block_id: str = Field(pattern=r"^QB-[0-9]{2}$")
    assessments: list[CandidateAssessment] = Field(min_length=3, max_length=3)
    block_note: str

    @model_validator(mode="after")
    def candidates_are_unique(self) -> "BlockReview":
        ids = [item.public_text_id for item in self.assessments]
        if len(set(ids)) != 3:
            raise ValueError("each block must contain three distinct candidates")
        return self


class PairReview(Strict):
    public_pair_id: str = Field(pattern=r"^QB-[0-9]{2}-[LW]$")
    target_mode: Literal["literary", "web_fiction"]
    candidate_ids: list[str] = Field(min_length=2, max_length=2)
    target_mode_winners: list[str] = Field(min_length=1, max_length=2)
    lower_ai_taste_winners: list[str] = Field(min_length=1, max_length=2)
    evidence: list[ParagraphEvidence] = Field(min_length=1)

    @model_validator(mode="after")
    def winners_are_candidates(self) -> "PairReview":
        candidates = set(self.candidate_ids)
        if len(candidates) != 2:
            raise ValueError("pair candidates must be distinct")
        for winners in (self.target_mode_winners, self.lower_ai_taste_winners):
            if len(set(winners)) != len(winners) or not set(winners) <= candidates:
                raise ValueError("pair winner outside candidates")
        return self


class RootCauseBlindReview(Strict):
    schema_version: Literal["style-root-cause-blind-review-v0"]
    reviewer_id: str = Field(pattern=r"^RC-BLIND-REVIEWER-[0-9]{2}$")
    scope: ReviewScope
    blocks: list[BlockReview] = Field(min_length=4, max_length=4)
    pairs: list[PairReview] = Field(min_length=8, max_length=8)
    cross_block_observations: list[str]

    @model_validator(mode="after")
    def coverage_is_unique(self) -> "RootCauseBlindReview":
        block_ids = [item.public_block_id for item in self.blocks]
        pair_ids = [item.public_pair_id for item in self.pairs]
        if len(set(block_ids)) != 4 or len(set(pair_ids)) != 8:
            raise ValueError("review block or pair coverage is not unique")
        return self


def validate_review_against_public(review: RootCauseBlindReview, public: dict) -> None:
    expected_blocks = {
        block["public_block_id"]: {candidate["public_text_id"] for candidate in block["candidates"]}
        for block in public["blocks"]
    }
    actual_blocks = {
        block.public_block_id: {assessment.public_text_id for assessment in block.assessments}
        for block in review.blocks
    }
    if expected_blocks != actual_blocks:
        raise ValueError("review block coverage differs from public material")
    expected_pairs = {
        pair["public_pair_id"]: (pair["pair_type"], set(pair["candidate_ids"]))
        for pair in public["pairs"]
    }
    actual_pairs = {
        pair.public_pair_id: (pair.target_mode, set(pair.candidate_ids))
        for pair in review.pairs
    }
    if expected_pairs != actual_pairs:
        raise ValueError("review pair coverage differs from public material")

