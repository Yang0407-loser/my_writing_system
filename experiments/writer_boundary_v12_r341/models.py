from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Severity = Literal["P0", "P1", "P2", "P3"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Independence(Strict):
    independent_fresh_conversation: Literal[True]
    inherited_project_history: Literal[False]
    other_r3_4_reviews_accessed: Literal[False]
    implementation_modified: Literal[False]
    external_or_story_model_called: Literal[False]
    network_requests_sent: Literal[False]


class TestRun(Strict):
    test_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    result: Literal["pass", "fail"]
    evidence: str = Field(min_length=1)


class Finding(Strict):
    finding_id: str = Field(min_length=1)
    severity: Severity
    title: str = Field(min_length=1)
    evidence: list[str]
    reproduction: list[str]
    impact: str = Field(min_length=1)
    minimal_fix: list[str]


class SeverityCounts(Strict):
    P0: int = Field(ge=0)
    P1: int = Field(ge=0)
    P2: int = Field(ge=0)
    P3: int = Field(ge=0)


class Recommendation(Strict):
    capability_probe_layer_build_recommended: bool
    reason: str = Field(min_length=1)


class FrozenAuthorization(Strict):
    authorized_scope: Literal["independent_r3_4_generation_package_audit_only"]
    capability_probe_layer_build_authorized: Literal[False]
    capability_probe_call_authorized: Literal[False]
    real_generation_authorized: Literal[False]
    model_call_authorized: Literal[False]


class R341IndependentReview(Strict):
    schema_version: Literal["1.2-r3.4.1-independent-review"]
    reviewer_id: str = Field(pattern=r"^R341-INDEPENDENT-REVIEWER-[0-9]{2}$")
    scope: list[str]
    independence: Independence
    tests_run: list[TestRun]
    findings: list[Finding]
    out_of_scope_observations: list[str]
    severity_counts: SeverityCounts
    verdict: Literal["pass", "fail"]
    recommendation: Recommendation
    authorization: FrozenAuthorization

    @model_validator(mode="after")
    def validate_semantics(self) -> "R341IndependentReview":
        actual = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
        for finding in self.findings:
            actual[finding.severity] += 1
        if self.severity_counts.model_dump() != actual:
            raise ValueError("severity_counts must be mechanically derived from findings")
        expected_verdict = "fail" if actual["P0"] or actual["P1"] else "pass"
        if self.verdict != expected_verdict:
            raise ValueError("verdict must be fail iff any P0/P1 finding exists")
        if self.verdict == "fail" and self.recommendation.capability_probe_layer_build_recommended:
            raise ValueError("a failing review cannot recommend capability-probe layer build")
        return self
