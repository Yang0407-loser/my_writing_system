from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Confidence = Annotated[int, Field(strict=True, ge=1, le=5)]
WitnessKind = Literal[
    "process_log",
    "direct_explanation",
    "abstract_emotion",
    "event_overengineering",
    "logistics_dialogue",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SelectedDecisions(StrictModel):
    initial_risk_check: Literal["check_album_first", "check_power_capacity_first"]
    catalog_temporary_handling: Literal["manual_blotting", "sealed_dry_box"]
    expand_focus: list[
        Literal[
            "adhesive_instability",
            "battery_limit",
            "catalog_temporary_handling",
        ]
    ] = Field(min_length=2, max_length=2)
    dialogue_jobs: list[
        Literal[
            "risk_confirmation",
            "priority_choice",
            "responsibility_boundary",
            "open_ending",
        ]
    ] = Field(min_length=4, max_length=4)
    emotion_channels: list[
        Literal[
            "hesitation",
            "waiting_for_confirmation",
            "object_handling",
            "unfinished_action",
            "silence",
        ]
    ] = Field(min_length=2, max_length=3)
    ending_state: Literal["temporary_only"]
    relationship_delta: Literal["none"]
    new_characters: Literal["none"]
    new_solution: Literal["none"]

    @model_validator(mode="after")
    def validate_sets(self) -> "SelectedDecisions":
        if len(set(self.expand_focus)) != 2:
            raise ValueError("expand_focus must contain two distinct choices")
        if set(self.dialogue_jobs) != {
            "risk_confirmation",
            "priority_choice",
            "responsibility_boundary",
            "open_ending",
        }:
            raise ValueError("dialogue_jobs must contain the four allowed jobs exactly")
        if len(set(self.emotion_channels)) != len(self.emotion_channels):
            raise ValueError("emotion_channels must be unique")
        return self


class DecisionTicket(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    scene_id: Literal["SC3"] = "SC3"
    repeat: Annotated[int, Field(ge=1, le=2)]
    source_contract_hash: str
    selected_decisions: SelectedDecisions
    content_facts_added: list[str] = Field(default_factory=list, max_length=0)
    locked: Literal[True] = True
    ticket_hash: str


class UsageRecord(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    finish_reason: str


class WitnessReview(StrictModel):
    category: WitnessKind
    detected: bool
    paragraphs: list[str] = Field(default_factory=list)
    description: str = ""

    @model_validator(mode="after")
    def require_evidence(self) -> "WitnessReview":
        if self.detected and (not self.paragraphs or not self.description.strip()):
            raise ValueError("detected witness requires paragraph IDs and description")
        return self


class HardChecks(StrictModel):
    mandatory_events_complete: bool
    new_character: bool
    new_solution: bool
    relationship_change: bool
    temporary_ending: bool
    decision_fidelity: bool


class SampleReview(StrictModel):
    text_id: str
    hard_checks: HardChecks
    witnesses: list[WitnessReview] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def witness_set(self) -> "SampleReview":
        expected = {
            "process_log",
            "direct_explanation",
            "abstract_emotion",
            "event_overengineering",
            "logistics_dialogue",
        }
        if {row.category for row in self.witnesses} != expected:
            raise ValueError("all five witness categories are required exactly once")
        return self


class PairPreference(StrictModel):
    pair_id: str
    naturalness: Literal["text_1", "text_2", "tie"]
    less_template: Literal["text_1", "text_2", "tie"]
    character_credibility: Literal["text_1", "text_2", "tie"]
    emotional_residue: Literal["text_1", "text_2", "tie"]
    overall_quality: Literal["text_1", "text_2", "tie"]
    more_mechanical: Literal["text_1", "text_2", "tie"]
    confidence: Confidence


class ReviewScope(StrictModel):
    independent_new_task: Literal[True]
    blind_key_accessed: Literal[False]
    other_reviews_accessed: Literal[False]
    prompts_tickets_results_accessed: Literal[False]
    public_material_only: Literal[True]


class CanaryReview(StrictModel):
    reviewer_id: str = Field(min_length=1)
    scope: ReviewScope
    samples: list[SampleReview] = Field(min_length=4, max_length=4)
    pairs: list[PairPreference] = Field(min_length=2, max_length=2)
