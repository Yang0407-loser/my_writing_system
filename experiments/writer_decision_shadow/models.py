from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SourceType = Literal[
    "scene_prompt",
    "shared_character",
    "shared_world_fact",
    "mandatory_event",
    "forbidden_event",
    "scene_modulation",
    "style_signature",
]
DecisionCategory = Literal[
    "mandatory_event",
    "state_delta",
    "relationship_authority",
    "character_authority",
    "fact_authority",
    "closure_state",
]
VerificationMode = Literal["presence", "absence", "state_match", "authority_check"]
TopologyCategory = Literal[
    "action_expansion",
    "process_compression",
    "dialogue_function",
    "emotion_channel",
    "direct_explanation",
]
HardStatus = Literal[
    "present",
    "absent",
    "contradicted",
    "unverifiable",
    "respected",
    "violated",
]
SoftStatus = Literal["pass", "borderline", "fail"]
UnauthorizedCategory = Literal[
    "new_character_causal_authority",
    "new_solution",
    "unapproved_relationship_change",
    "new_responsibility_or_commitment",
    "unsourced_object_or_quantity_fact",
    "direct_relationship_explanation",
]
ProcessCategory = Literal[
    "repeated_transport",
    "itemized_inventory",
    "continuous_counting",
    "cost_accounting",
    "logistics_exposition",
]
Confidence = Annotated[int, Field(strict=True, ge=1, le=5)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(StrictModel):
    ref_id: str
    source_type: SourceType
    source_path: str
    source_sha256: str


class DecisionObligation(StrictModel):
    decision_id: str
    category: DecisionCategory
    claim: str
    allowed_values: list[str] = Field(min_length=1)
    expected_state: str
    source_refs: list[str] = Field(min_length=1)
    verification_mode: VerificationMode
    hard: Literal[True] = True


class StyleTopologyObligation(StrictModel):
    decision_id: str
    category: TopologyCategory
    claim: str
    source_refs: list[str] = Field(min_length=1)
    verification_scale: Literal["pass_borderline_fail"] = "pass_borderline_fail"
    hard: Literal[False] = False


class SceneDecisionTicket(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    ticket_id: str
    scene_id: str
    source_contract_hash: str
    source_refs: list[SourceRef] = Field(min_length=1)
    hard_obligations: list[DecisionObligation] = Field(min_length=1)
    soft_topology_obligations: list[StyleTopologyObligation] = Field(min_length=1)
    relationship_delta: Literal["none"] = "none"
    new_content_facts: list[str] = Field(default_factory=list, max_length=0)
    content_authority_owner: Literal["upstream_scene_contract"] = (
        "upstream_scene_contract"
    )
    ticket_token_estimate: int = Field(ge=1)
    compact_rendering: str
    compact_rendering_hash: str
    deterministic: Literal[True] = True
    ticket_hash: str

    @model_validator(mode="after")
    def validate_sources(self) -> "SceneDecisionTicket":
        known = {item.ref_id for item in self.source_refs}
        if len(known) != len(self.source_refs):
            raise ValueError("source ref IDs must be unique")
        for obligation in [
            *self.hard_obligations,
            *self.soft_topology_obligations,
        ]:
            unknown = set(obligation.source_refs) - known
            if unknown:
                raise ValueError(
                    f"{obligation.decision_id} has unknown refs: {sorted(unknown)}"
                )
        return self


class ParagraphRecord(StrictModel):
    paragraph_id: str
    text: str
    sha256: str


class ShadowSample(StrictModel):
    blind_id: str
    scene_code: str
    text_sha256: str
    paragraph_separator: Literal["\n\n"] = "\n\n"
    paragraphs: list[ParagraphRecord] = Field(min_length=1)


class ShadowCorpus(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    source_public_sha256: str
    sample_count: int
    samples: list[ShadowSample]


class ReviewScope(StrictModel):
    independent_shadow_review: Literal[True]
    blind_key_accessed: Literal[False]
    prior_blind_reviews_accessed: Literal[False]
    other_shadow_reviews_accessed: Literal[False]


class HardWitnessReview(StrictModel):
    decision_id: str
    status: HardStatus
    evidence_paragraphs: list[str] = Field(default_factory=list)
    contradiction_paragraphs: list[str] = Field(default_factory=list)
    violation_paragraphs: list[str] = Field(default_factory=list)
    confidence: Confidence
    comment: str = ""


class SoftWitnessReview(StrictModel):
    decision_id: str
    status: SoftStatus
    evidence_paragraphs: list[str] = Field(default_factory=list)
    violation_paragraphs: list[str] = Field(default_factory=list)
    confidence: Confidence
    comment: str = ""


class UnauthorizedContentReview(StrictModel):
    category: UnauthorizedCategory
    detected: bool
    paragraphs: list[str] = Field(default_factory=list)
    description: str = ""


class ProcessLogReview(StrictModel):
    category: ProcessCategory
    detected: bool
    paragraphs: list[str] = Field(default_factory=list)
    description: str = ""


class SampleWitnessReview(StrictModel):
    blind_id: str
    hard_obligations: list[HardWitnessReview]
    soft_topology: list[SoftWitnessReview]
    unauthorized_content: list[UnauthorizedContentReview]
    process_log_checks: list[ProcessLogReview]
    overall_comment: str = ""


class ValidatorReview(StrictModel):
    reviewer_id: str = Field(min_length=1)
    review_scope: ReviewScope
    samples: list[SampleWitnessReview]
