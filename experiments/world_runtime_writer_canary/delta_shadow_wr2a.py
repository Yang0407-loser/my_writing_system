"""WR2-A typed-delta contracts and read-only transition validation shadow.

The first batch consumes a manually evidence-anchored gold extraction from the
eight frozen WR1-P outputs.  It validates transition semantics without calling
a model, rewriting prose, or committing canonical state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".world_runtime_wr1p_canary_runtime"
GOLD = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr2a_wr1p_delta_gold_v1.json"
DEFAULT_REPORT = ROOT / "reports/world-runtime-wr2a-delta-shadow-result-2026-08-04.json"
CONTRACT_VERSION = "world-runtime-typed-delta-shadow-wr2a-v1"

ChangeType = Literal[
    "storefront_public_sale",
    "knowledge_state",
    "resignation_acknowledgement",
    "unsourced_project_fact",
    "object_state",
    "repeated_completed_event",
    "employment_state",
]
ValidationOutcome = Literal["valid", "invalid", "unresolved"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceAnchor(FrozenModel):
    evidence_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    occurrence: int = Field(default=1, ge=1)


class EvidenceSpan(FrozenModel):
    evidence_id: str
    claim: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    excerpt: str

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("evidence span end must follow start")
        return self


class ProposedChange(FrozenModel):
    change_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    change_type: ChangeType
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


class ProposedTypedDelta(FrozenModel):
    delta_id: str
    sample_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
    scene_id: str
    project_id: str
    state_variant: Literal["before", "after", "after_augmented"]
    base_revision: int = Field(ge=0)
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: tuple[EvidenceSpan, ...] = ()
    changes: tuple[ProposedChange, ...] = ()
    consumer: Literal["transition_validator_shadow"] = "transition_validator_shadow"
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
        for change in self.changes:
            if not set(change.evidence_ids).issubset(known):
                raise ValueError("change references unknown evidence")
        return self


class ValidationItem(FrozenModel):
    change_id: str
    outcome: ValidationOutcome
    rule_ids: tuple[str, ...] = ()
    unresolved_fact_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def explain_nonvalid(self):
        if self.outcome == "invalid" and not self.rule_ids:
            raise ValueError("invalid transition requires rule IDs")
        if self.outcome == "unresolved" and not self.unresolved_fact_ids:
            raise ValueError("unresolved transition requires unresolved facts")
        return self


class ShadowValidation(FrozenModel):
    validation_id: str
    delta_id: str
    sample_id: str
    base_revision: int
    output_hash: str
    items: tuple[ValidationItem, ...] = ()
    accepted_change_ids: tuple[str, ...] = ()
    rejected_change_ids: tuple[str, ...] = ()
    unresolved_change_ids: tuple[str, ...] = ()
    would_commit: Literal[False] = False
    state_mutated: Literal[False] = False
    downstream_consumer: Literal["wr2a_audit_report"] = "wr2a_audit_report"
    schema_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION

    @model_validator(mode="after")
    def partitions(self):
        expected = {
            "valid": set(self.accepted_change_ids),
            "invalid": set(self.rejected_change_ids),
            "unresolved": set(self.unresolved_change_ids),
        }
        for outcome, ids in expected.items():
            actual = {item.change_id for item in self.items if item.outcome == outcome}
            if actual != ids:
                raise ValueError(f"{outcome} partition mismatch")
        if any(expected[left] & expected[right] for left, right in (("valid", "invalid"), ("valid", "unresolved"), ("invalid", "unresolved"))):
            raise ValueError("validation partitions overlap")
        return self


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _span(text: str, anchor: EvidenceAnchor) -> EvidenceSpan:
    cursor = 0
    start = -1
    for _ in range(anchor.occurrence):
        start = text.find(anchor.excerpt, cursor)
        if start < 0:
            raise ValueError(f"unsupported evidence: {anchor.evidence_id}")
        cursor = start + len(anchor.excerpt)
    return EvidenceSpan(
        evidence_id=anchor.evidence_id,
        claim=anchor.claim,
        start=start,
        end=start + len(anchor.excerpt),
        excerpt=anchor.excerpt,
    )


def _fact(state, fact_id: str):
    return next((item for item in state.facts if item.fact_id == fact_id), None)


def load_gold_deltas() -> tuple[ProposedTypedDelta, ...]:
    payload = _read(GOLD)
    _, states, _ = wr1r._artifacts()
    deltas = []
    for item in payload["items"]:
        text_path = RUNTIME / "private/outputs" / f"{item['sample_id']}.txt"
        text = text_path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != item["output_hash"]:
            raise ValueError(f"WR2-A output hash mismatch: {item['sample_id']}")
        state = states[item["state_variant"]]
        if state.revision != item["base_revision"]:
            raise ValueError(f"WR2-A base revision mismatch: {item['sample_id']}")
        evidence = tuple(_span(text, EvidenceAnchor.model_validate(value)) for value in item["evidence"])
        changes = tuple(ProposedChange.model_validate(value) for value in item["changes"])
        deltas.append(
            ProposedTypedDelta(
                delta_id=f"delta:wr2a:{item['sample_id']}",
                sample_id=item["sample_id"],
                scene_id=item["scene_id"],
                project_id="project:saturday-bakery",
                state_variant=item["state_variant"],
                base_revision=item["base_revision"],
                output_hash=digest,
                evidence=evidence,
                changes=changes,
            )
        )
    return tuple(deltas)


def validate_delta(delta: ProposedTypedDelta) -> ShadowValidation:
    constitution, states, _ = wr1r._artifacts()
    state = states[delta.state_variant]
    if delta.base_revision != state.revision:
        raise ValueError("shadow validator base revision mismatch")
    rules = {item.semantic_key: item.rule_id for item in constitution.rules}
    evidence_ids = {item.evidence_id for item in delta.evidence}
    items = []
    for change in delta.changes:
        if not set(change.evidence_ids).issubset(evidence_ids):
            raise ValueError("shadow validator evidence reference mismatch")
        if change.change_type == "storefront_public_sale":
            time = _fact(state, "fact:clock:time")
            opens = _fact(state, "fact:bakery:opens-at")
            storefront = _fact(state, "fact:bakery:storefront")
            if time and opens and storefront and time.value < opens.value and storefront.value == "closed":
                item = ValidationItem(
                    change_id=change.change_id,
                    outcome="invalid",
                    rule_ids=(rules["bakery.storefront.schedule"], rules["storefront.public_opening.schedule"]),
                    reasons=("public sale occurs before 06:00 while storefront is closed",),
                    evidence_ids=change.evidence_ids,
                )
            else:
                item = ValidationItem(change_id=change.change_id, outcome="valid", reasons=("sale is within active public-opening state",), evidence_ids=change.evidence_ids)
        elif change.change_type == "knowledge_state":
            if change.mechanism == "explicit_group_send_and_body_response":
                item = ValidationItem(change_id=change.change_id, outcome="valid", rule_ids=(rules["publication.public_reaction.reach"],), reasons=("text shows both transmission and recipient perception",), evidence_ids=change.evidence_ids)
            else:
                item = ValidationItem(change_id=change.change_id, outcome="invalid", rule_ids=(rules["publication.public_reaction.reach"],), reasons=("knowledge change lacks an explicit transmission path",), evidence_ids=change.evidence_ids)
        elif change.change_type == "resignation_acknowledgement":
            before = _fact(state, "fact:company:acknowledgement")
            if before and before.epistemic_status == "unknown" and change.mechanism == "institutional_reply" and change.actor == "company:hr-system":
                item = ValidationItem(change_id=change.change_id, outcome="valid", reasons=("institutional acknowledgement is explicitly received in the final text",), evidence_ids=change.evidence_ids)
            else:
                item = ValidationItem(change_id=change.change_id, outcome="invalid", rule_ids=(rules["employment.termination.prerequisite"],), reasons=("acknowledgement transition lacks institutional evidence",), evidence_ids=change.evidence_ids)
        elif change.change_type == "unsourced_project_fact":
            item = ValidationItem(
                change_id=change.change_id,
                outcome="unresolved",
                unresolved_fact_ids=(f"unresolved:{change.subject}:{change.predicate}",),
                reasons=("text introduces a persistent project fact absent from canonical state",),
                evidence_ids=change.evidence_ids,
            )
        elif change.change_type == "object_state":
            if change.mechanism == "missing_actor_or_event":
                item = ValidationItem(
                    change_id=change.change_id,
                    outcome="invalid",
                    rule_ids=("kernel.state_change_requires_mechanism",),
                    reasons=("object state changes while the location is explicitly unoccupied and no actor or event is shown",),
                    evidence_ids=change.evidence_ids,
                )
            else:
                item = ValidationItem(
                    change_id=change.change_id,
                    outcome="valid",
                    reasons=("object state change has an explicit actor and mechanism",),
                    evidence_ids=change.evidence_ids,
                )
        elif change.change_type == "repeated_completed_event":
            item = ValidationItem(
                change_id=change.change_id,
                outcome="invalid",
                rule_ids=("kernel.delta_is_idempotent",),
                reasons=("text repeats an event already recorded as completed in the base state",),
                evidence_ids=change.evidence_ids,
            )
        elif change.change_type == "employment_state":
            acknowledgement = _fact(state, "fact:company:acknowledgement")
            employment = _fact(state, "fact:employment:state")
            if (
                change.after_value == "ended"
                and employment
                and employment.value == "employed"
                and acknowledgement
                and acknowledgement.epistemic_status == "unknown"
            ):
                item = ValidationItem(
                    change_id=change.change_id,
                    outcome="invalid",
                    rule_ids=(rules["employment.termination.prerequisite"],),
                    reasons=("employment is still active and company acknowledgement is unknown",),
                    evidence_ids=change.evidence_ids,
                )
            else:
                item = ValidationItem(
                    change_id=change.change_id,
                    outcome="valid",
                    reasons=("employment transition prerequisites are satisfied",),
                    evidence_ids=change.evidence_ids,
                )
        else:
            raise ValueError(f"unsupported WR2-A change type: {change.change_type}")
        items.append(item)
    accepted = tuple(item.change_id for item in items if item.outcome == "valid")
    rejected = tuple(item.change_id for item in items if item.outcome == "invalid")
    unresolved = tuple(item.change_id for item in items if item.outcome == "unresolved")
    return ShadowValidation(
        validation_id=f"validation:wr2a:{delta.sample_id}",
        delta_id=delta.delta_id,
        sample_id=delta.sample_id,
        base_revision=delta.base_revision,
        output_hash=delta.output_hash,
        items=tuple(items),
        accepted_change_ids=accepted,
        rejected_change_ids=rejected,
        unresolved_change_ids=unresolved,
    )


def run_shadow(output_path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    payload = _read(GOLD)
    expected = {item["sample_id"]: item["expected_outcomes"] for item in payload["items"]}
    deltas = load_gold_deltas()
    validations = tuple(validate_delta(delta) for delta in deltas)
    mismatches = []
    for validation in validations:
        actual = {item.change_id: item.outcome for item in validation.items}
        if actual != expected[validation.sample_id]:
            mismatches.append({"sample_id": validation.sample_id, "expected": expected[validation.sample_id], "actual": actual})
    result = {
        "schema_version": "world-runtime-delta-shadow-audit-wr2a-v1",
        "status": "gold_contract_and_validator_shadow_complete",
        "gold_fixture_sha256": hashlib.sha256(GOLD.read_bytes()).hexdigest(),
        "sample_count": len(deltas),
        "samples_with_changes": sum(bool(item.changes) for item in deltas),
        "proposed_change_count": sum(len(item.changes) for item in deltas),
        "valid_change_count": sum(len(item.accepted_change_ids) for item in validations),
        "invalid_change_count": sum(len(item.rejected_change_ids) for item in validations),
        "unresolved_change_count": sum(len(item.unresolved_change_ids) for item in validations),
        "gold_validation_mismatches": mismatches,
        "output_hash_binding_complete": True,
        "evidence_span_binding_complete": True,
        "state_mutations": 0,
        "commits": 0,
        "model_calls": 0,
        "deltas": [item.model_dump(mode="json") for item in deltas],
        "validations": [item.model_dump(mode="json") for item in validations],
        "next_gate": "automatic_extractor_shadow_not_started",
    }
    _write(output_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = run_shadow(args.output)
    summary = {key: result[key] for key in (
        "status", "gold_fixture_sha256", "sample_count", "samples_with_changes",
        "proposed_change_count", "valid_change_count", "invalid_change_count",
        "unresolved_change_count", "gold_validation_mismatches", "state_mutations",
        "commits", "model_calls", "next_gate",
    )}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
