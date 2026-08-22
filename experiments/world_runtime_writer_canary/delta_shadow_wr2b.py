"""WR2-B expanded typed-delta ontology and read-only transition validator.

This module does not modify the frozen WR2-A contracts.  It adds transition
families exposed by the adversarial development partition while preserving the
same single-consumer, no-commit boundary.
"""

from __future__ import annotations

from datetime import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2a import EvidenceSpan


CONTRACT_VERSION = "world-runtime-typed-delta-shadow-wr2b-v2"

ChangeTypeV2 = Literal[
    "storefront_public_sale",
    "storefront_public_handoff",
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
]
ValidationOutcome = Literal["valid", "invalid", "unresolved"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProposedChangeV2(FrozenModel):
    change_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    change_type: ChangeTypeV2
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


class ProposedTypedDeltaV2(FrozenModel):
    delta_id: str
    sample_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
    scene_id: str
    project_id: str
    state_variant: Literal["before", "after", "after_augmented"]
    base_revision: int = Field(ge=0)
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: tuple[EvidenceSpan, ...] = ()
    changes: tuple[ProposedChangeV2, ...] = ()
    consumer: Literal["transition_validator_shadow_v2"] = "transition_validator_shadow_v2"
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


class ValidationItemV2(FrozenModel):
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


class ShadowValidationV2(FrozenModel):
    validation_id: str
    delta_id: str
    sample_id: str
    base_revision: int
    output_hash: str
    items: tuple[ValidationItemV2, ...] = ()
    accepted_change_ids: tuple[str, ...] = ()
    rejected_change_ids: tuple[str, ...] = ()
    unresolved_change_ids: tuple[str, ...] = ()
    would_commit: Literal[False] = False
    state_mutated: Literal[False] = False
    downstream_consumer: Literal["wr2b_development_audit"] = "wr2b_development_audit"
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


def _fact(state, fact_id: str):
    return next((item for item in state.facts if item.fact_id == fact_id), None)


def _clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour=hour, minute=minute)


def validate_delta_v2(delta: ProposedTypedDeltaV2) -> ShadowValidationV2:
    constitution, states, _ = wr1r._artifacts()
    state = states[delta.state_variant]
    if delta.base_revision != state.revision:
        raise ValueError("WR2-B validator base revision mismatch")
    rules = {item.semantic_key: item.rule_id for item in constitution.rules}
    evidence_ids = {item.evidence_id for item in delta.evidence}
    items: list[ValidationItemV2] = []
    acknowledgement_available = bool(
        (ack := _fact(state, "fact:company:acknowledgement"))
        and ack.epistemic_status == "confirmed_true"
        and ack.value is True
    )

    for change in delta.changes:
        if not set(change.evidence_ids).issubset(evidence_ids):
            raise ValueError("WR2-B validator evidence reference mismatch")
        common = {"change_id": change.change_id, "evidence_ids": change.evidence_ids}

        if change.change_type in {"storefront_public_sale", "storefront_public_handoff"}:
            current = _fact(state, "fact:clock:time")
            opens = _fact(state, "fact:bakery:opens-at")
            storefront = _fact(state, "fact:bakery:storefront")
            before_open = bool(current and opens and _clock(current.value) < _clock(opens.value))
            if before_open and storefront and storefront.value == "closed":
                item = ValidationItemV2(
                    **common,
                    outcome="invalid",
                    rule_ids=(rules["bakery.storefront.schedule"], rules["storefront.public_opening.schedule"]),
                    reasons=("public goods handoff occurs before opening while the storefront is closed",),
                )
            else:
                item = ValidationItemV2(**common, outcome="valid", reasons=("public handoff occurs within an allowed opening state",))
        elif change.change_type == "knowledge_state":
            if change.mechanism in {
                "explicit_group_send_and_body_response",
                "group_file_send_and_body_response",
                "private_link_send_and_body_response",
            }:
                item = ValidationItemV2(**common, outcome="valid", reasons=("transmission and body perception are both evidenced",))
            else:
                item = ValidationItemV2(
                    **common,
                    outcome="invalid",
                    rule_ids=(rules["publication.public_reaction.reach"],),
                    reasons=("knowledge change lacks a supported transmission and perception path",),
                )
        elif change.change_type == "resignation_acknowledgement":
            if change.mechanism == "institutional_reply" and change.actor == "company:hr-system":
                item = ValidationItemV2(**common, outcome="valid", reasons=("institutional acknowledgement is explicit",))
                acknowledgement_available = True
            else:
                item = ValidationItemV2(
                    **common,
                    outcome="invalid",
                    rule_ids=(rules["employment.termination.prerequisite"],),
                    reasons=("acknowledgement is not issued by an institutional channel",),
                )
        elif change.change_type == "unsourced_project_fact":
            item = ValidationItemV2(
                **common,
                outcome="unresolved",
                unresolved_fact_ids=(f"unresolved:{change.subject}:{change.predicate}",),
                reasons=("persistent project fact has no canonical source",),
            )
        elif change.change_type == "object_state":
            if change.mechanism == "missing_actor_or_event":
                item = ValidationItemV2(
                    **common,
                    outcome="invalid",
                    rule_ids=("kernel.state_change_requires_mechanism",),
                    reasons=("object changes without an actor or declared natural event",),
                )
            else:
                item = ValidationItemV2(**common, outcome="valid", reasons=("object transition has an explicit causal mechanism",))
        elif change.change_type == "repeated_completed_event":
            item = ValidationItemV2(
                **common,
                outcome="invalid",
                rule_ids=("kernel.delta_is_idempotent",),
                reasons=("completed event is executed again",),
            )
        elif change.change_type == "employment_state":
            if change.after_value == "ended" and change.mechanism == "acknowledged_effective_resignation" and acknowledgement_available:
                item = ValidationItemV2(**common, outcome="valid", reasons=("institutional acknowledgement and effective resignation precede employment end",))
            elif change.after_value == "ended":
                item = ValidationItemV2(
                    **common,
                    outcome="invalid",
                    rule_ids=(rules["employment.termination.prerequisite"],),
                    reasons=("employment end lacks an acknowledged effective resignation",),
                )
            else:
                item = ValidationItemV2(**common, outcome="valid", reasons=("employment transition is supported",))
        elif change.change_type == "publication_state":
            prior = _fact(state, "fact:article:status")
            if prior and prior.value == "draft" and change.after_value == "published" and change.mechanism == "submit_and_platform_publish":
                item = ValidationItemV2(**common, outcome="valid", reasons=("submission and platform publication are both explicit",))
            else:
                item = ValidationItemV2(
                    **common,
                    outcome="invalid",
                    rule_ids=(rules["publication.public_visibility.prerequisite"],),
                    reasons=("publication transition lacks submission or platform confirmation",),
                )
        elif change.change_type == "resignation_delivery":
            if change.mechanism == "institutional_email_delivery":
                item = ValidationItemV2(**common, outcome="valid", reasons=("resignation reaches the institutional HR channel",))
            else:
                item = ValidationItemV2(
                    **common,
                    outcome="invalid",
                    rule_ids=(rules["employment.resignation.private_draft_delivery"],),
                    reasons=("resignation lacks institutional delivery",),
                )
        elif change.change_type == "resignation_personal_record":
            item = ValidationItemV2(**common, outcome="valid", reasons=("private copy creates only a personal record and does not imply company delivery",))
        elif change.change_type == "clock_state":
            prior = _fact(state, "fact:clock:time")
            if prior and _clock(change.after_value) >= _clock(prior.value) and change.mechanism == "explicit_time_progression":
                item = ValidationItemV2(**common, outcome="valid", reasons=("explicit scene time advances monotonically",))
            else:
                item = ValidationItemV2(
                    **common,
                    outcome="invalid",
                    rule_ids=("kernel.state_change_requires_mechanism",),
                    reasons=("clock change is unsupported or moves backward",),
                )
        elif change.change_type == "location_state":
            if change.mechanism == "explicit_entry":
                item = ValidationItemV2(**common, outcome="valid", reasons=("character location changes through explicit entry",))
            else:
                item = ValidationItemV2(
                    **common,
                    outcome="invalid",
                    rule_ids=("kernel.state_change_requires_mechanism",),
                    reasons=("location change lacks a movement event",),
                )
        else:
            raise ValueError(f"unsupported WR2-B change type: {change.change_type}")
        items.append(item)

    return ShadowValidationV2(
        validation_id=f"validation:wr2b:{delta.sample_id}",
        delta_id=delta.delta_id,
        sample_id=delta.sample_id,
        base_revision=delta.base_revision,
        output_hash=delta.output_hash,
        items=tuple(items),
        accepted_change_ids=tuple(item.change_id for item in items if item.outcome == "valid"),
        rejected_change_ids=tuple(item.change_id for item in items if item.outcome == "invalid"),
        unresolved_change_ids=tuple(item.change_id for item in items if item.outcome == "unresolved"),
    )

