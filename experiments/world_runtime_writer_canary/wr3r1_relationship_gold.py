"""WR3R1 relationship gold: closed offline chain for relationship_state.

Freezes a closed gold chain that adds two character relationships
(lin-wan<->zhou-ye 青梅竹马, ji-qing<->lin-wan 大学闺蜜) as
``relationship:{a}:{b}`` facts, plus one rejected self-relationship change.
The chain proves the WR ontology can carry relationships end-to-end
(proposed -> validated -> committed) without any LLM call.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import Field, model_validator

from app.writing.world_runtime_bakery_gold import (
    BAKERY_PROJECT_ID,
    ChangeKind,
    GoldChangeValidation,
    GoldCommittedStateDelta,
    GoldEvidence,
    GoldOutcome,
    GoldValidationResult,
    ProposedFactChange,
    ProposedStateDelta,
)
from app.writing.world_runtime_contracts import (
    CanonicalWorldState,
    FrozenRuntimeModel,
    ProvenanceRef,
    WorldFact,
    canonical_hash,
)


RELATIONSHIP_GOLD_VERSION = "world-runtime-relationship-gold-wr3r1-v1"

FINAL_TEXT = (
    "周野站在操作台前揉面，林晚推门进来。他们从小一起长大，是青梅竹马，"
    "只是上周因为周野忘记约定，两人之间生出了一道裂缝。季晴端着咖啡过来，"
    "提起大学时代，她和林晚还是无话不谈的闺蜜。"
)

_EVIDENCE_1 = "ev:rel:lin-wan-zhou-ye"
_EVIDENCE_2 = "ev:rel:ji-qing-lin-wan"
_EVIDENCE_3 = "ev:rel:self-invalid"

_RELATIONSHIPS: dict[str, dict[str, Any]] = {
    "relationship:lin-wan:zhou-ye": {
        "relation_type": "青梅竹马",
        "direction": "complex",
        "intensity": 7,
        "stage": "因周野疏忽产生裂痕",
        "description": "青梅竹马的两人因周野的疏忽产生矛盾",
        "evidence": _EVIDENCE_1,
    },
    "relationship:ji-qing:lin-wan": {
        "relation_type": "大学闺蜜",
        "direction": "positive",
        "intensity": 6,
        "stage": "互相支持",
        "description": "林晚的大学闺蜜，互联网大厂 HRBP",
        "evidence": _EVIDENCE_2,
    },
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _provenance() -> ProvenanceRef:
    return ProvenanceRef(
        source_id="final-text:wr3r1-relationship-gold",
        source_type="final_text",
        source_hash=_sha256(FINAL_TEXT),
        producer="wr3r1_relationship_gold",
    )


def _after_facts() -> tuple[WorldFact, ...]:
    facts = []
    sequence = 0
    for subject, predicates in sorted(_RELATIONSHIPS.items()):
        for predicate in (
            "relation_type",
            "direction",
            "intensity",
            "stage",
            "description",
        ):
            sequence += 1
            facts.append(WorldFact(
                fact_id=f"fact:{subject.replace(':', '-')}:{predicate}",
                subject=subject,
                predicate=predicate,
                value=predicates[predicate],
                epistemic_status="confirmed_true",
                authority="text_extracted",
                provenance=_provenance(),
                revision=8,
            ))
    return tuple(facts)


def _change(
    sequence: int,
    subject: str,
    predicate: str,
    after_value: Any,
    evidence_id: str,
) -> ProposedFactChange:
    return ProposedFactChange(
        change_id=f"rel-change-{sequence:02d}",
        sequence=sequence,
        kind="fact_update",
        fact_id=f"fact:{subject.replace(':', '-')}:{predicate}",
        subject=subject,
        predicate=predicate,
        before_value=None,
        before_epistemic_status="unknown",
        after_value=after_value,
        after_epistemic_status="confirmed_true",
        actor="narrator",
        evidence_ids=(evidence_id,),
    )


def _proposed_changes() -> tuple[ProposedFactChange, ...]:
    changes = []
    sequence = 0
    for subject, predicates in sorted(_RELATIONSHIPS.items()):
        for predicate, value in predicates.items():
            if predicate == "evidence":
                continue
            sequence += 1
            changes.append(_change(
                sequence,
                subject,
                predicate,
                value,
                predicates["evidence"],
            ))
    sequence += 1
    changes.append(ProposedFactChange(
        change_id="rel-change-self-invalid",
        sequence=sequence,
        kind="fact_update",
        fact_id="fact:relationship-lin-wan-lin-wan:relation_type",
        subject="relationship:lin-wan:lin-wan",
        predicate="relation_type",
        before_value=None,
        before_epistemic_status="unknown",
        after_value="自己",
        after_epistemic_status="confirmed_true",
        actor="narrator",
        evidence_ids=(_EVIDENCE_3,),
    ))
    return tuple(changes)


def _validation_items() -> tuple[GoldChangeValidation, ...]:
    items = []
    for change in _proposed_changes():
        if change.change_id == "rel-change-self-invalid":
            items.append(GoldChangeValidation(
                change_id=change.change_id,
                outcome="invalid",
                rule_ids=("kernel.relationship.subject_shape",),
                evidence_ids=change.evidence_ids,
                reasons=("self-referencing relationship subject is not canonical",),
            ))
        else:
            items.append(GoldChangeValidation(
                change_id=change.change_id,
                outcome="valid",
                evidence_ids=change.evidence_ids,
                reasons=("relationship change conforms to the kernel relationship contract",),
            ))
    return tuple(items)


def _evidence() -> tuple[GoldEvidence, ...]:
    return tuple(
        GoldEvidence(
            evidence_id=evidence_id,
            source_type="final_text",
            source_id="final-text:wr3r1-relationship-gold",
            source_hash=_sha256(FINAL_TEXT),
            excerpt=FINAL_TEXT,
            start=0,
            end=len(FINAL_TEXT),
        )
        for evidence_id in (_EVIDENCE_1, _EVIDENCE_2, _EVIDENCE_3)
    )


class RelationshipGoldFixture(FrozenRuntimeModel):
    fixture_id: str = "world-runtime-wr3r1:relationship:v1"
    project_id: str = BAKERY_PROJECT_ID
    state_before: CanonicalWorldState
    final_text: str = Field(min_length=1)
    output_hash: str = Field(min_length=1)
    evidence: tuple[GoldEvidence, ...] = Field(min_length=1)
    proposed_delta: ProposedStateDelta
    validation_result: GoldValidationResult
    committed_delta: GoldCommittedStateDelta
    state_after: CanonicalWorldState
    schema_version: str = RELATIONSHIP_GOLD_VERSION

    @model_validator(mode="after")
    def audit_closed_gold_chain(self):
        if self.output_hash != _sha256(self.final_text):
            raise ValueError("final text hash mismatch")
        if self.state_before.revision != 7 or self.state_after.revision != 8:
            raise ValueError("relationship gold must advance revision 7 -> 8")
        artifact_hashes = {
            self.proposed_delta.output_hash,
            self.validation_result.output_hash,
            self.committed_delta.output_hash,
            self.output_hash,
        }
        if len(artifact_hashes) != 1:
            raise ValueError("delta/validation/commit must bind the final output hash")
        if self.committed_delta.base_revision != self.state_before.revision:
            raise ValueError("committed delta base revision mismatch")
        if self.committed_delta.after_revision != self.state_after.revision:
            raise ValueError("committed delta after revision mismatch")
        committed_ids = {
            change.change_id for change in self.committed_delta.changes
        }
        if not committed_ids.issubset(self.validation_result.accepted_change_ids):
            raise ValueError("committed changes must be accepted by validation")
        rejected = {
            item.change_id
            for item in self.validation_result.items
            if item.outcome == "invalid"
        }
        if rejected != set(self.validation_result.rejected_change_ids):
            raise ValueError("validation rejected partition mismatch")
        if not rejected:
            raise ValueError("relationship gold must include one rejected change")
        after_by_id = {fact.fact_id: fact for fact in self.state_after.facts}
        for change in self.committed_delta.changes:
            fact = after_by_id.get(change.fact_id)
            if fact is None or fact.value != change.after_value:
                raise ValueError("committed change missing from state_after")
        return self


def build_relationship_gold_fixture() -> RelationshipGoldFixture:
    output_hash = _sha256(FINAL_TEXT)
    proposed_changes = _proposed_changes()
    committed_changes = tuple(
        change for change in proposed_changes
        if change.change_id != "rel-change-self-invalid"
    )
    validation_items = _validation_items()
    accepted = tuple(
        item.change_id for item in validation_items if item.outcome == "valid"
    )
    rejected = tuple(
        item.change_id for item in validation_items if item.outcome == "invalid"
    )
    delta = ProposedStateDelta(
        delta_id="delta:wr3r1-relationship-gold",
        project_id=BAKERY_PROJECT_ID,
        base_revision=7,
        output_hash=output_hash,
        changes=proposed_changes,
    )
    validation = GoldValidationResult(
        validation_id="validation:wr3r1-relationship-gold",
        project_id=BAKERY_PROJECT_ID,
        delta_id=delta.delta_id,
        base_revision=7,
        output_hash=output_hash,
        items=validation_items,
        accepted_change_ids=accepted,
        rejected_change_ids=rejected,
    )
    committed = GoldCommittedStateDelta(
        commit_id="commit:wr3r1-relationship-gold:r8",
        project_id=BAKERY_PROJECT_ID,
        delta_id=delta.delta_id,
        validation_id=validation.validation_id,
        base_revision=7,
        after_revision=8,
        output_hash=output_hash,
        idempotency_key="wr3r1:relationship-gold",
        changes=committed_changes,
    )
    return RelationshipGoldFixture(
        project_id=BAKERY_PROJECT_ID,
        state_before=CanonicalWorldState(
            project_id=BAKERY_PROJECT_ID,
            revision=7,
            facts=(),
        ),
        final_text=FINAL_TEXT,
        output_hash=output_hash,
        evidence=_evidence(),
        proposed_delta=delta,
        validation_result=validation,
        committed_delta=committed,
        state_after=CanonicalWorldState(
            project_id=BAKERY_PROJECT_ID,
            revision=8,
            facts=_after_facts(),
        ),
    )


def relationship_committable(gold: RelationshipGoldFixture):
    """Convert the relationship gold committed delta into committer inputs."""
    from app.writing.world_runtime_state_committer import (
        CommittableChange,
        CommittableDelta,
        CommittableValidation,
        CommittableValidationItem,
    )

    evidence_ids = set()
    changes = []
    for sequence, change in enumerate(gold.committed_delta.changes, 1):
        evidence_ids.update(change.evidence_ids)
        changes.append(CommittableChange(
            change_id=change.change_id,
            sequence=sequence,
            change_type="relationship_state",
            subject=change.subject,
            predicate=change.predicate,
            before_value=None,
            before_epistemic_status="unknown",
            after_value=change.after_value,
            actor=change.actor,
            mechanism="relationship_revealed",
            evidence_ids=change.evidence_ids,
        ))
    delta = CommittableDelta(
        delta_id="delta:wr3r1-gold",
        project_id=gold.project_id,
        base_revision=gold.state_before.revision,
        output_hash=gold.output_hash,
        evidence_ids=tuple(sorted(evidence_ids)),
        changes=tuple(changes),
    )
    validation = CommittableValidation(
        validation_id="validation:wr3r1-gold",
        delta_id=delta.delta_id,
        base_revision=delta.base_revision,
        output_hash=delta.output_hash,
        items=tuple(
            CommittableValidationItem(
                change_id=change.change_id,
                outcome="valid",
                evidence_ids=change.evidence_ids,
            )
            for change in changes
        ),
        accepted_change_ids=tuple(change.change_id for change in changes),
    )
    return delta, validation
