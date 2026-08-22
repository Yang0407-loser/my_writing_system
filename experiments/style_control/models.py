from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Recovered from bc3c9d4:app/models.py.  Despite the historical "50D" name,
# the initial production class enumerated 49 non-metadata controls.  The
# experiment preserves that discrepancy instead of silently inventing a field.
HISTORICAL_STYLE_FIELDS: tuple[str, ...] = (
    "narrative_density",
    "primary_emotion",
    "emotion_intensity",
    "emotion_subtlety",
    "emotion_blend",
    "emotion_curve",
    "emotional_peaks",
    "catharsis_style",
    "narrative_empathy",
    "inner_monologue_ratio",
    "show_vs_tell",
    "emotional_registry",
    "sensory_anchoring",
    "emotional_contrast",
    "short_sentence_ratio",
    "medium_sentence_ratio",
    "long_sentence_ratio",
    "sentence_length_variance",
    "sentence_pattern",
    "sentence_opening_style",
    "complex_sentence_ratio",
    "paragraph_rhythm",
    "paragraph_length_avg",
    "paragraph_opening_style",
    "dialogue_ratio",
    "dialogue_mixing",
    "dialogue_tag_style",
    "pacing",
    "scene_transition",
    "time_dilation",
    "tension_curve",
    "metaphor_frequency",
    "simile_metaphor_ratio",
    "personification",
    "synesthesia",
    "rhetorical_devices",
    "rhetorical_density",
    "vocabulary_register",
    "vocabulary_richness",
    "chengyu_frequency",
    "dialect_flavor",
    "foreign_loanwords",
    "adjective_density",
    "adverb_policy",
    "modifier_position",
    "sensory_density",
    "sensory_spectrum",
    "color_use",
    "imagery_domain",
)


class EvidenceItem(BaseModel):
    principle_id: str
    excerpt: str
    explanation: str


class NegativeExample(BaseModel):
    text: str
    reason: str


class StyleContract(BaseModel):
    contract_version: str = "1.0"
    positive_principles: list[str] = Field(min_length=3, max_length=5)
    prohibitions: list[str] = Field(min_length=3, max_length=5)
    narrative_distance_and_viewpoint: str
    sentence_and_paragraph_rhythm: str
    diction_imagery_and_sensory_sources: str
    emotional_expression: str
    scene_adaptation: dict[str, str]
    positive_examples: list[str] = Field(min_length=2, max_length=3)
    negative_examples: list[NegativeExample] = Field(min_length=1, max_length=2)
    evidence: list[EvidenceItem]


class CompletionMetadata(BaseModel):
    finish_reason: str = "unknown"
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_seconds: float | None = None


class ExperimentSample(BaseModel):
    sample_id: str
    style_id: str
    scene_id: str
    arm: Literal["A", "B", "C", "D"]
    repeat: int
    seed: int | None
    target_chars: int
    reference_path: str
    prompt_path: str
    result_path: str
    status: Literal["planned", "mock_completed", "completed", "failed"] = "planned"
    model: str
    temperature: float
    max_tokens: int
    estimated_input_tokens: int
    style_input_hash: str
    failure: str | None = None
    metadata: CompletionMetadata | None = None


class PreparedStyleInput(BaseModel):
    style_id: str
    reference_sha256: str
    source: Literal["mock", "llm"]
    four_dimensional: dict[str, Any]
    historical_profile: dict[str, Any]
    historical_unavailable_fields: list[str]
    historical_brief: str
    style_contract: StyleContract


class StyleSignature(BaseModel):
    """Schema-v2 style-only contract.

    Global prose quality rules, scene modulation, demonstrations and reference
    evidence deliberately live outside this model.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    style_id: str
    active_dimensions: list[str] = Field(min_length=5, max_length=8)
    narrative_distance: str
    viewpoint_permissions: str
    sentence_rhythm: str
    paragraph_rhythm: str
    dialogue_function: str
    dialogue_turn_pattern: str
    emotional_mediation: str
    diction_register: str
    imagery_domain: str
    sensory_priority: str
    distinctive_prohibitions: list[str] = Field(min_length=1, max_length=6)
    discriminators: list[str] = Field(min_length=3, max_length=6)

    @model_validator(mode="after")
    def validate_active_dimensions(self) -> "StyleSignature":
        allowed = {
            "narrative_distance",
            "viewpoint_permissions",
            "sentence_rhythm",
            "paragraph_rhythm",
            "dialogue_function",
            "dialogue_turn_pattern",
            "emotional_mediation",
            "diction_register",
            "imagery_domain",
            "sensory_priority",
        }
        unknown = set(self.active_dimensions) - allowed
        if unknown:
            raise ValueError(f"unknown active_dimensions: {sorted(unknown)}")
        if len(set(self.active_dimensions)) != len(self.active_dimensions):
            raise ValueError("active_dimensions must be unique")
        return self


class StyleDemonstration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    demonstration_id: str
    mechanism: str
    text: str


class StyleDemonstrations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    positive_demonstrations: list[StyleDemonstration] = Field(default_factory=list, max_length=3)
    negative_demonstrations: list[StyleDemonstration] = Field(default_factory=list, max_length=2)
    negative_reasons: list[str] = Field(default_factory=list, max_length=2)
    max_demonstration_tokens: int = Field(default=500, ge=1, le=800)


class StyleEvidence(BaseModel):
    """Audit-only reference evidence. Never accepted by prompt builders."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    reference_sha256: str
    items: list[EvidenceItem] = Field(default_factory=list)
    protected_terms: list[str] = Field(default_factory=list)


class PreparedAblationStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    style_id: str
    style_signature: StyleSignature
    style_demonstrations: StyleDemonstrations
    evidence: StyleEvidence
