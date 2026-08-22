"""Adapter from WR2-C5 typed delta / validation to committable inputs.

WR2-C6 adds chain normalization: the projector declares every change's
before_value relative to the project's original base state, while the committer
requires each change's declared before to match the state at application time.
This adapter rewrites accepted changes into chain-consistent before values
using the original base state and the state the commit starts from, and rejects
declared values that match neither (integrity check).  When a chained
``before_state`` is supplied, the committable base revision is also advanced to
``before_state.revision`` so the commit matches the state it actually applies
to.
"""

from __future__ import annotations

from typing import Any

from app.writing.world_runtime_contracts import CanonicalWorldState
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


ADAPTER_VERSION = "world-runtime-state-commit-adapter-wr2c6-v1"


def _state_by_key(state: CanonicalWorldState) -> dict[tuple[str, str], tuple[Any, str]]:
    return {
        (fact.subject, fact.predicate): (fact.value, fact.epistemic_status)
        for fact in state.facts
    }


def _rewrite_chain_before(
    changes: tuple[CommittableChange, ...],
    accepted_ids: set[str],
    *,
    base_state: CanonicalWorldState,
    before_state: CanonicalWorldState,
) -> tuple[CommittableChange, ...]:
    """Rewrite accepted changes so declared before values match the working state."""
    working = _state_by_key(before_state)
    base = _state_by_key(base_state)
    rewritten: list[CommittableChange] = []
    for change in changes:
        if change.change_id not in accepted_ids:
            rewritten.append(change)
            continue
        key = (change.subject, change.predicate)
        declared = (change.before_value, change.before_epistemic_status)
        current = working.get(key)
        if current is not None:
            effective = current
            allowed = declared == effective or (key in base and declared == base[key])
            if not allowed and key not in base:
                allowed = declared == (None, "unknown")
            if not allowed:
                raise ValueError(
                    f"change {change.change_id} before value mismatch "
                    "with base and working state"
                )
            if declared != effective:
                change = change.model_copy(update={
                    "before_value": effective[0],
                    "before_epistemic_status": effective[1],
                })
            working[key] = (change.after_value, "confirmed_true")
        else:
            if declared != (None, "unknown"):
                raise ValueError(
                    f"change {change.change_id} cannot declare an existing "
                    "before for a new fact"
                )
            working[key] = (change.after_value, "confirmed_true")
        rewritten.append(change)
    return tuple(rewritten)


def to_committable(
    delta: ProposedTypedDeltaV5,
    validation: ShadowValidationV5,
    *,
    project_id: str | None = None,
    base_state: CanonicalWorldState | None = None,
    before_state: CanonicalWorldState | None = None,
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
    if (base_state is None) != (before_state is None):
        raise ValueError("base_state and before_state must be provided together")
    if base_state is not None and before_state is not None:
        changes = _rewrite_chain_before(
            changes,
            set(validation.accepted_change_ids),
            base_state=base_state,
            before_state=before_state,
        )
    commit_revision = before_state.revision if before_state is not None else delta.base_revision
    committable_delta = CommittableDelta(
        delta_id=delta.delta_id,
        project_id=project_id or delta.project_id,
        base_revision=commit_revision,
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
        base_revision=commit_revision,
        output_hash=validation.output_hash,
        items=items,
        accepted_change_ids=validation.accepted_change_ids,
        rejected_change_ids=validation.rejected_change_ids,
        unresolved_change_ids=validation.unresolved_change_ids,
    )
    return committable_delta, committable_validation
