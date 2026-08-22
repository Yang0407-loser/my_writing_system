"""WR3R1 typed-delta validator: relationship_state added to the WR ontology.

Extends the WR2-C6 validator with a 15th change type ``relationship_state``.
Non-relationship changes are delegated to ``validate_delta_v6`` unchanged;
relationship changes are checked against the kernel relationship contract
(canonical subject shape, known predicates, direction enum, intensity range).
Legality is decided here; nothing in this module can commit state.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2a import EvidenceSpan
from experiments.world_runtime_writer_canary.delta_shadow_wr2c6 import (
    CONTRACT_VERSION as WR2C6_CONTRACT_VERSION,
    ProposedChangeV5,
    ProposedTypedDeltaV5,
    validate_delta_v6,
)
from app.writing.world_runtime_relationship_projection import (
    RELATIONSHIP_PREDICATES,
    relationship_ids,
)


CONTRACT_VERSION = "world-runtime-typed-delta-shadow-wr3r1-v1"

ChangeTypeV3R1 = Literal[
    "storefront_public_sale",
    "storefront_public_handoff",
    "storefront_operation_state",
    "knowledge_state",
    "resignation_acknowledgement",
    "unsourced_project_fact",
    "object_state",
    "repeated_completed_event",
    "employment_state",
    "publication_state",
    "resignation_delivery",
    "resignation_personal_record",
    "clock_state",
    "location_state",
    "relationship_state",
]
ValidationOutcome = Literal["valid", "invalid", "unresolved"]

_DIRECTIONS = {"positive", "negative", "complex"}


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProposedChangeV3R1(FrozenModel):
    change_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    change_type: ChangeTypeV3R1
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    before_value: Any = None
    before_epistemic_status: Literal["confirmed_true", "unknown"]
    after_value: Any = None
    actor: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    event_id: str | None = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    authority: Literal["text_extracted"] = "text_extracted"


class ProposedTypedDeltaV3R1(FrozenModel):
    delta_id: str = Field(min_length=1)
    sample_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
    scene_id: str
    project_id: str
    state_variant: Literal["before", "after", "after_augmented"]
    base_revision: int = Field(ge=0)
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: tuple[EvidenceSpan, ...] = ()
    changes: tuple[ProposedChangeV3R1, ...] = ()
    consumer: Literal["transition_validator_shadow_v5"] = "transition_validator_shadow_v5"
    commit_sink: Literal["forbidden"] = "forbidden"
    schema_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION

    @model_validator(mode="after")
    def closed_references(self):
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        change_ids = [item.change_id for item in self.changes]
        sequences = [item.sequence for item in self.changes]
        if len(change_ids) != len(set(change_ids)):
            raise ValueError("change IDs must be unique")
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("change sequences must be contiguous from one")
        known = set(evidence_ids)
        if any(not set(change.evidence_ids).issubset(known) for change in self.changes):
            raise ValueError("change references unknown evidence")
        return self


class ValidationItemV3R1(FrozenModel):
    change_id: str
    outcome: ValidationOutcome
    rule_ids: tuple[str, ...] = ()
    unresolved_fact_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def complete_reasoning(self):
        if self.outcome == "invalid" and not self.rule_ids:
            raise ValueError("invalid transition requires rule IDs")
        if self.outcome == "unresolved" and not self.unresolved_fact_ids:
            raise ValueError("unresolved transition requires unresolved facts")
        return self


class ShadowValidationV3R1(FrozenModel):
    validation_id: str
    delta_id: str
    sample_id: str
    base_revision: int
    output_hash: str
    items: tuple[ValidationItemV3R1, ...] = ()
    accepted_change_ids: tuple[str, ...] = ()
    rejected_change_ids: tuple[str, ...] = ()
    unresolved_change_ids: tuple[str, ...] = ()
    would_commit: Literal[False] = False
    state_mutated: Literal[False] = False
    downstream_consumer: Literal["wr3r1_development_audit"] = "wr3r1_development_audit"
    schema_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION

    @model_validator(mode="after")
    def partitions(self):
        actual = {
            outcome: {item.change_id for item in self.items if item.outcome == outcome}
            for outcome in ("valid", "invalid", "unresolved")
        }
        expected = {
            "valid": set(self.accepted_change_ids),
            "invalid": set(self.rejected_change_ids),
            "unresolved": set(self.unresolved_change_ids),
        }
        if actual != expected:
            raise ValueError("validation partition mismatch")
        return self


def _relationship_issues(change: ProposedChangeV3R1) -> tuple[str, ...]:
    issues: list[str] = []
    if relationship_ids(change.subject) is None:
        issues.append("kernel.relationship.subject_shape")
    if change.predicate not in RELATIONSHIP_PREDICATES:
        issues.append("kernel.relationship.predicate_unknown")
    if change.predicate == "direction" and change.after_value not in _DIRECTIONS:
        issues.append("kernel.relationship.direction_enum")
    if change.predicate == "intensity":
        value = change.after_value
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        if not isinstance(value, int) or not 0 <= value <= 10:
            issues.append("kernel.relationship.intensity_range")
    if change.predicate == "relation_type" and not str(change.after_value or "").strip():
        issues.append("kernel.relationship.relation_type_required")
    return tuple(issues)


def _relationship_items(changes: tuple[ProposedChangeV3R1, ...]) -> list[ValidationItemV3R1]:
    items = []
    for change in changes:
        common = {"change_id": change.change_id, "evidence_ids": change.evidence_ids}
        issues = _relationship_issues(change)
        if issues:
            items.append(ValidationItemV3R1(
                **common,
                outcome="invalid",
                rule_ids=issues,
                reasons=("relationship kernel contract violated: " + ";".join(issues),),
            ))
        else:
            items.append(ValidationItemV3R1(
                **common,
                outcome="valid",
                reasons=("relationship change conforms to the kernel relationship contract",),
            ))
    return items


def validate_delta_v3r1(
    delta: ProposedTypedDeltaV3R1,
    *,
    state=None,
) -> ShadowValidationV3R1:
    """Validate a typed delta with relationship_state support (chained mode)."""
    if state is None:
        _, states, _ = wr1r._artifacts()
        state = states[delta.state_variant]
    if delta.base_revision != state.revision:
        raise ValueError("WR3R1 validator base revision mismatch")
    relationship_changes = tuple(
        change for change in delta.changes
        if change.change_type == "relationship_state"
    )
    other_changes = tuple(
        change for change in delta.changes
        if change.change_type != "relationship_state"
    )
    items: list[ValidationItemV3R1] = []
    if other_changes:
        renumbered = tuple(
            change.model_copy(update={"sequence": index})
            for index, change in enumerate(other_changes, 1)
        )
        v5_delta = ProposedTypedDeltaV5.model_validate({
            **{
                key: value
                for key, value in delta.model_dump(exclude={"changes"}).items()
                if key != "schema_version"
            },
            "schema_version": WR2C6_CONTRACT_VERSION,
            "changes": [
                ProposedChangeV5.model_validate(change.model_dump())
                for change in renumbered
            ],
        })
        base = validate_delta_v6(v5_delta, state=state)
        items.extend(
            ValidationItemV3R1.model_validate(item.model_dump())
            for item in base.items
        )
    items.extend(_relationship_items(relationship_changes))
    return ShadowValidationV3R1(
        validation_id=f"{delta.delta_id}:wr3r1",
        delta_id=delta.delta_id,
        sample_id=delta.sample_id,
        base_revision=delta.base_revision,
        output_hash=delta.output_hash,
        items=tuple(items),
        accepted_change_ids=tuple(
            item.change_id for item in items if item.outcome == "valid"
        ),
        rejected_change_ids=tuple(
            item.change_id for item in items if item.outcome == "invalid"
        ),
        unresolved_change_ids=tuple(
            item.change_id for item in items if item.outcome == "unresolved"
        ),
    )
