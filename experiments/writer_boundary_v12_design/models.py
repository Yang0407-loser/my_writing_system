from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from experiments.writer_boundary_v12_preflight.models import (
    EvidenceBearingHardChecks,
    PostWriteExecutionAudit,
    SolutionBoundaryPolicy,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DesignSolutionOption(StrictModel):
    value: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    allowed_implementation_details: list[str] = Field(min_length=2)


class DesignDecisionContract(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.2-design"] = "1.2-design"
    scene_id: str = Field(pattern=r"^SC[5-8]$")
    decision_id: str = Field(min_length=1)
    choose_exactly: Literal[1] = 1
    allowed_values: list[DesignSolutionOption] = Field(min_length=2, max_length=2)
    solution_boundary_policy: SolutionBoundaryPolicy

    @model_validator(mode="after")
    def distinct_options(self) -> "DesignDecisionContract":
        if len({item.value for item in self.allowed_values}) != 2:
            raise ValueError("each scene requires two distinct whitelist values")
        return self


class StyleSignature(StrictModel):
    narrative_distance: str = Field(min_length=1)
    sentence_rhythm: str = Field(min_length=1)
    paragraph_rhythm: str = Field(min_length=1)
    dialogue_function: str = Field(min_length=1)
    emotional_mediation: str = Field(min_length=1)
    diction_register: str = Field(min_length=1)


class DesignScene(StrictModel):
    schema_version: Literal["1.2-design"] = "1.2-design"
    scene_id: str = Field(pattern=r"^SC[5-8]$")
    structural_axis: Literal[
        "heat_deterioration",
        "power_service_continuity",
        "contamination_safety",
        "confidentiality_custody",
    ]
    scene: str = Field(min_length=1)
    characters: dict[str, str]
    world_facts: list[str] = Field(min_length=3)
    primary_obligation: str = Field(min_length=1)
    resource_constraint: str = Field(min_length=1)
    long_term_problem: str = Field(min_length=1)
    mandatory_events: list[str] = Field(min_length=6, max_length=6)
    forbidden_events: list[str] = Field(min_length=8, max_length=8)
    style_signature: StyleSignature
    target_chars: int = Field(ge=800, le=1400)
    decision_contract: DesignDecisionContract

    @model_validator(mode="after")
    def aligned_scene_and_events(self) -> "DesignScene":
        if len(self.characters) != 2:
            raise ValueError("each scene must contain exactly two named characters")
        if self.decision_contract.scene_id != self.scene_id:
            raise ValueError("scene and decision contract IDs must match")
        expected_m = [f"M{i}" for i in range(1, 7)]
        actual_m = [item.split(maxsplit=1)[0] for item in self.mandatory_events]
        if actual_m != expected_m:
            raise ValueError("mandatory events must be ordered M1 through M6")
        expected_f = [f"F{i}" for i in range(1, 9)]
        actual_f = [item.split(maxsplit=1)[0] for item in self.forbidden_events]
        if actual_f != expected_f:
            raise ValueError("forbidden events must be ordered F1 through F8")
        return self


class ExperimentDesign(StrictModel):
    schema_version: Literal["1.2-design"] = "1.2-design"
    experiment_id: Literal["writer-boundary-v1-2"]
    enabled: Literal[False] = False
    routes: list[Literal["W0", "W1"]] = Field(min_length=2, max_length=2)
    repeats_per_route: Literal[3] = 3
    base_seed: int
    scenes: list[DesignScene] = Field(min_length=4, max_length=4)
    primary_pair_rule: Literal["same_scene_repeat_and_observed_solution"]
    unmatched_pair_policy: Literal["retain_as_route_diagnostic_exclude_from_preference_denominator"]
    model_calls_allowed: Literal[False] = False

    @model_validator(mode="after")
    def balanced_design(self) -> "ExperimentDesign":
        if self.routes != ["W0", "W1"]:
            raise ValueError("routes must be exactly W0 then W1")
        if len({scene.scene_id for scene in self.scenes}) != 4:
            raise ValueError("four unique scene IDs are required")
        if len({scene.structural_axis for scene in self.scenes}) != 4:
            raise ValueError("four distinct structural axes are required")
        return self


class V12PostWriteReviewRecord(StrictModel):
    schema_version: Literal["1.2-review"] = "1.2-review"
    text_id: str = Field(pattern=r"^T\d{2}$")
    scene_id: str = Field(pattern=r"^SC[5-8]$")
    paragraph_count: int = Field(ge=1)
    hard_checks: EvidenceBearingHardChecks
    execution_audit: PostWriteExecutionAudit
