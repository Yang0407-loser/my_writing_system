from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ParagraphId = str
SolutionSignal = Literal[
    "replaces_whitelist_solution",
    "adds_independent_protection_layer",
    "adds_resource_that_independently_changes_risk",
    "creates_second_disposition_path",
    "changes_resource_or_authority_boundary",
    "resolves_long_term_problem",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SolutionOption(StrictModel):
    value: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    allowed_implementation_details: list[str] = Field(min_length=1)


class SolutionBoundaryPolicy(StrictModel):
    allowed_detail_definition: str = Field(min_length=1)
    additional_candidate_definition: str = Field(min_length=1)
    confirmed_new_solution_min_signals: Literal[2] = 2
    confirmation_signals: list[SolutionSignal] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def unique_signals(self) -> "SolutionBoundaryPolicy":
        if len(set(self.confirmation_signals)) != 6:
            raise ValueError("six confirmation signals must be unique")
        return self


class SharedDecisionContract(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.2-preflight"] = "1.2-preflight"
    scene_id: Literal["SC4"] = "SC4"
    decision_id: Literal["store_item_temporary_handling"]
    choose_exactly: Literal[1] = 1
    allowed_values: list[SolutionOption] = Field(min_length=2, max_length=2)
    solution_boundary_policy: SolutionBoundaryPolicy

    @model_validator(mode="after")
    def unique_options(self) -> "SharedDecisionContract":
        if len({item.value for item in self.allowed_values}) != 2:
            raise ValueError("two distinct solution values are required")
        return self


def _validate_evidence(paragraphs: list[str], description: str) -> None:
    if not paragraphs or not description.strip():
        raise ValueError("evidence paragraphs and description are required")
    if any(not p.startswith("P") or not p[1:].isdigit() for p in paragraphs):
        raise ValueError("paragraph IDs must use P plus digits")


class EvidenceCheck(StrictModel):
    status: bool
    evidence_paragraphs: list[ParagraphId]
    description: str

    @model_validator(mode="after")
    def evidence_required(self) -> "EvidenceCheck":
        _validate_evidence(self.evidence_paragraphs, self.description)
        return self


class MandatoryEventsCheck(EvidenceCheck):
    failed_event_ids: list[Literal["M1", "M2", "M3", "M4", "M5", "M6"]]

    @model_validator(mode="after")
    def failure_ids_match_status(self) -> "MandatoryEventsCheck":
        if self.status and self.failed_event_ids:
            raise ValueError("passing mandatory check cannot list failed events")
        if not self.status and not self.failed_event_ids:
            raise ValueError("failed mandatory check must list failed event IDs")
        return self


class SolutionCandidate(StrictModel):
    candidate: str = Field(min_length=1)
    classification: Literal[
        "allowed_implementation_detail",
        "additional_solution_candidate",
        "confirmed_new_solution",
    ]
    evidence_paragraphs: list[ParagraphId]
    description: str
    signals: list[SolutionSignal] = Field(default_factory=list)

    @model_validator(mode="after")
    def classification_threshold(self) -> "SolutionCandidate":
        _validate_evidence(self.evidence_paragraphs, self.description)
        if len(set(self.signals)) != len(self.signals):
            raise ValueError("solution signals must be unique")
        if self.classification == "allowed_implementation_detail" and self.signals:
            raise ValueError("allowed detail must not carry independent-solution signals")
        if self.classification == "additional_solution_candidate" and not self.signals:
            raise ValueError("additional candidate requires at least one signal")
        if self.classification == "confirmed_new_solution" and len(self.signals) < 2:
            raise ValueError("confirmed new solution requires at least two signals")
        return self


class NewSolutionCheck(EvidenceCheck):
    candidates: list[SolutionCandidate]

    @model_validator(mode="after")
    def status_matches_candidates(self) -> "NewSolutionCheck":
        confirmed = any(x.classification == "confirmed_new_solution" for x in self.candidates)
        if self.status != confirmed:
            raise ValueError("new_solution status must match confirmed candidate presence")
        return self


class EvidenceBearingHardChecks(StrictModel):
    mandatory_events_complete: MandatoryEventsCheck
    new_character: EvidenceCheck
    new_solution: NewSolutionCheck
    relationship_change: EvidenceCheck
    temporary_ending: EvidenceCheck
    boundary_fidelity: EvidenceCheck


class ObservedTemporarySolution(StrictModel):
    value: str = Field(min_length=1)
    evidence_paragraphs: list[ParagraphId]
    description: str

    @model_validator(mode="after")
    def evidence_required(self) -> "ObservedTemporarySolution":
        _validate_evidence(self.evidence_paragraphs, self.description)
        return self


class PostWriteExecutionAudit(StrictModel):
    primary_obligation: EvidenceCheck
    observed_temporary_solution: ObservedTemporarySolution
    additional_solution_candidates: list[SolutionCandidate]
    resource_constraint_preserved: EvidenceCheck
    long_term_problem_unresolved: EvidenceCheck


class PreflightReviewRecord(StrictModel):
    schema_version: Literal["1.2-preflight"] = "1.2-preflight"
    text_id: str = Field(min_length=1)
    hard_checks: EvidenceBearingHardChecks
    execution_audit: PostWriteExecutionAudit

