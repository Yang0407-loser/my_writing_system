from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Confidence = Annotated[int, Field(strict=True, ge=1, le=5)]
OriginalWitness = Literal["process_log", "direct_explanation", "abstract_emotion", "event_overengineering", "logistics_dialogue"]
StructuralDiagnostic = Literal["obligation_sequence_visibility", "dialogue_slot_visibility", "local_choice_starvation", "over_complete_structure", "constraint_reconfirmation"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LockedBoundaries(StrictModel):
    priority_object: Literal["customer_field_diary"]
    store_item_temporary_handling: Literal["raised_mesh_rack", "single_absorbent_wrap"]
    long_term_problem: Literal["unresolved"]
    relationship_delta: Literal["none"]
    new_characters: Literal["none"]
    new_solution: Literal["none"]


class BoundaryTicket(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.1"] = "1.1"
    scene_id: Literal["SC4"] = "SC4"
    repeat: Annotated[int, Field(ge=1, le=2)]
    source_contract_hash: str
    locked_boundaries: LockedBoundaries
    content_facts_added: list[str] = Field(default_factory=list, max_length=0)
    locked: Literal[True] = True
    ticket_hash: str


class CompiledSummary(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    compiler_version: Literal["boundary-summary-1.0"] = "boundary-summary-1.0"
    source_ticket_hash: str
    compiled_summary: str
    summary_hash: str
    model_calls: Literal[0] = 0


class Witness(StrictModel):
    category: OriginalWitness | StructuralDiagnostic
    detected: bool
    paragraphs: list[str] = Field(default_factory=list)
    description: str = ""

    @model_validator(mode="after")
    def evidence(self) -> "Witness":
        if self.detected and (not self.paragraphs or not self.description.strip()):
            raise ValueError("detected=true requires paragraphs and description")
        if not self.detected and self.paragraphs:
            raise ValueError("detected=false requires empty paragraphs")
        return self


class HardChecks(StrictModel):
    mandatory_events_complete: bool
    new_character: bool
    new_solution: bool
    relationship_change: bool
    temporary_ending: bool
    boundary_fidelity: bool


class SampleReview(StrictModel):
    text_id: str
    hard_checks: HardChecks
    original_witnesses: list[Witness] = Field(min_length=5, max_length=5)
    structural_diagnostics: list[Witness] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def exact_categories(self) -> "SampleReview":
        if {x.category for x in self.original_witnesses} != {"process_log", "direct_explanation", "abstract_emotion", "event_overengineering", "logistics_dialogue"}:
            raise ValueError("five original witnesses required exactly once")
        if {x.category for x in self.structural_diagnostics} != {"obligation_sequence_visibility", "dialogue_slot_visibility", "local_choice_starvation", "over_complete_structure", "constraint_reconfirmation"}:
            raise ValueError("five structural diagnostics required exactly once")
        return self


Choice = Literal["text_1", "text_2", "tie"]


class PairReview(StrictModel):
    pair_id: str
    naturalness: Choice
    less_template: Choice
    character_credibility: Choice
    emotional_residue: Choice
    overall_quality: Choice
    more_mechanical: Choice
    confidence: Confidence


class ReviewScope(StrictModel):
    independent_new_task: Literal[True]
    blind_key_accessed: Literal[False]
    other_reviews_accessed: Literal[False]
    prompts_tickets_summaries_results_accessed: Literal[False]
    public_material_only: Literal[True]


class BoundaryReview(StrictModel):
    reviewer_id: str = Field(min_length=1)
    scope: ReviewScope
    samples: list[SampleReview] = Field(min_length=4, max_length=4)
    pairs: list[PairReview] = Field(min_length=2, max_length=2)

