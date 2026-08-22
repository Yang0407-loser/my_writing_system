"""Adapter from WR2-C5 typed delta / validation to committable inputs."""

from __future__ import annotations

from app.writing.world_runtime_state_committer import (
    CommittableChange,
    CommittableDelta,
    CommittableValidation,
    CommittableValidationItem,
)
from experiments.world_runtime_writer_canary.delta_shadow_wr2c5 import (
    ProposedTypedDeltaV5,
    ShadowValidationV5,
)


ADAPTER_VERSION = "world-runtime-state-commit-adapter-wr2c5-v1"


def to_committable(
    delta: ProposedTypedDeltaV5,
    validation: ShadowValidationV5,
    *,
    project_id: str | None = None,
) -> tuple[CommittableDelta, CommittableValidation]:
    """Convert one validated WR2-C5 delta into committer inputs."""

    changes = tuple(
        CommittableChange(
            change_id=change.change_id,
            sequence=change.sequence,
            change_type=change.change_type,
            subject=change.subject,
            predicate=change.predicate,
            before_value=change.before_value,
            before_epistemic_status=change.before_epistemic_status,
            after_value=change.after_value,
            actor=change.actor,
            mechanism=change.mechanism,
            evidence_ids=change.evidence_ids,
        )
        for change in delta.changes
    )
    committable_delta = CommittableDelta(
        delta_id=delta.delta_id,
        project_id=project_id or delta.project_id,
        base_revision=delta.base_revision,
        output_hash=delta.output_hash,
        evidence_ids=tuple(evidence.evidence_id for evidence in delta.evidence),
        changes=changes,
    )
    items = tuple(
        CommittableValidationItem(
            change_id=item.change_id,
            outcome=item.outcome,
            rule_ids=item.rule_ids,
            evidence_ids=item.evidence_ids,
        )
        for item in validation.items
    )
    committable_validation = CommittableValidation(
        validation_id=validation.validation_id,
        delta_id=validation.delta_id,
        base_revision=validation.base_revision,
        output_hash=validation.output_hash,
        items=items,
        accepted_change_ids=validation.accepted_change_ids,
        rejected_change_ids=validation.rejected_change_ids,
        unresolved_change_ids=validation.unresolved_change_ids,
    )
    return committable_delta, committable_validation
