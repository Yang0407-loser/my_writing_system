from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(Strict):
    independent_fresh_conversation: Literal[True]
    blind_key_accessed: Literal[False]
    other_reviews_accessed: Literal[False]
    private_material_accessed: Literal[False]
    prompts_or_arm_identity_accessed: Literal[False]
    public_material_only: Literal[True]
    external_or_story_model_called: Literal[False]


class Evidence(Strict):
    public_text_id: str = Field(pattern=r"^AQ[0-9]{2}$")
    paragraph_id: str = Field(pattern=r"^P[0-9]{2}$")
    explanation: str = Field(min_length=1)


class Assessment(Strict):
    public_text_id: str = Field(pattern=r"^AQ[0-9]{2}$")
    hard_task_complete: bool
    unauthorized_event_detected: bool
    commercial_momentum: int = Field(ge=1, le=5)
    character_motivation_credibility: int = Field(ge=1, le=5)
    specificity: int = Field(ge=1, le=5)
    naturalness: int = Field(ge=1, le=5)
    redundant_explanation: int = Field(ge=1, le=5)
    formulaic_expression: int = Field(ge=1, le=5)
    summary_closure: int = Field(ge=1, le=5)
    prompt_structure_leak: int = Field(ge=1, le=5)
    overall_ai_taste: int = Field(ge=1, le=5)
    evidence: list[Evidence] = Field(min_length=1)


class Block(Strict):
    public_block_id: str = Field(pattern=r"^AB-[0-9]{2}$")
    assessments: list[Assessment] = Field(min_length=2, max_length=2)
    better_commercial_execution: list[str] = Field(min_length=1, max_length=2)
    lower_ai_taste: list[str] = Field(min_length=1, max_length=2)
    better_overall: list[str] = Field(min_length=1, max_length=2)
    pair_evidence: list[Evidence] = Field(min_length=1)
    block_note: str

    @model_validator(mode="after")
    def validate_candidates(self) -> "Block":
        candidates = {item.public_text_id for item in self.assessments}
        if len(candidates) != 2:
            raise ValueError("block candidates must be distinct")
        for winners in (self.better_commercial_execution, self.lower_ai_taste, self.better_overall):
            if len(set(winners)) != len(winners) or not set(winners) <= candidates:
                raise ValueError("winner outside block candidates")
        if not all(item.public_text_id in candidates for item in self.pair_evidence):
            raise ValueError("pair evidence outside block candidates")
        return self


class AntiAIBlindReview(Strict):
    schema_version: Literal["style-anti-ai-blind-review-v0"]
    reviewer_id: str = Field(pattern=r"^AA-BLIND-REVIEWER-[0-9]{2}$")
    scope: Scope
    blocks: list[Block] = Field(min_length=4, max_length=4)
    cross_block_observations: list[str]

    @model_validator(mode="after")
    def unique_blocks(self) -> "AntiAIBlindReview":
        if len({item.public_block_id for item in self.blocks}) != 4:
            raise ValueError("block IDs must be unique")
        return self


def validate_review_against_public(review: AntiAIBlindReview, public: dict) -> None:
    expected = {b["public_block_id"]: {c["public_text_id"] for c in b["candidates"]} for b in public["blocks"]}
    actual = {b.public_block_id: {a.public_text_id for a in b.assessments} for b in review.blocks}
    if expected != actual:
        raise ValueError("review coverage differs from public material")

