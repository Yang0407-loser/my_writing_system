"""WR0-G on-demand confirmation, replay, and read-only debug projections.

This module turns explicit user actions into versioned constitution changes.
It never calls a model, edits production storage, or activates untouched
candidate-pack content.
"""

from __future__ import annotations

import json
from typing import Literal

import yaml
from pydantic import Field, model_validator

from .world_runtime_compiler import SceneRuntimeFrame
from .world_runtime_contracts import (
    CandidatePack,
    FrozenRuntimeModel,
    Lifecycle,
    LifecycleTransition,
    ProjectWorldConstitution,
    ProvenanceRef,
    RuleScope,
    StateEffect,
    StatePredicate,
    WorldRule,
    canonical_hash,
)
from .world_runtime_resolver import ResolvedWorldConstitution


WORLD_RUNTIME_CONFIRMATION_VERSION = "world-runtime-confirmation-wr0g-v1"

ConfirmationPriority = Literal["blocking", "high_impact", "provisional"]
ConfirmationDecision = Literal["confirm", "reject", "scoped_exception"]
ConfirmationArtifactType = Literal["rule", "lifecycle"]


class PendingConfirmationItem(FrozenRuntimeModel):
    item_id: str = Field(min_length=1)
    item_type: Literal["candidate_rule", "candidate_lifecycle", "resolver_conflict"]
    artifact_id: str = Field(min_length=1)
    semantic_key: str | None = None
    priority: ConfirmationPriority
    recommended_enforcement: Literal["block", "warn", "suggest"] | None = None
    reason: str = Field(min_length=1)
    source_hashes: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_CONFIRMATION_VERSION


class ConfirmationAction(FrozenRuntimeModel):
    action_id: str = Field(min_length=1)
    decision: ConfirmationDecision
    artifact_type: ConfirmationArtifactType
    artifact_id: str = Field(min_length=1)
    candidate_pack_ref: str | None = None
    scope: RuleScope
    enforcement: Literal["block", "warn", "suggest"] | None = None
    target_rule_id: str | None = None
    conditions: tuple[StatePredicate, ...] = ()
    effects: tuple[StateEffect, ...] = ()
    rationale: str = Field(min_length=1)
    user_input_hash: str = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_CONFIRMATION_VERSION

    @model_validator(mode="after")
    def validate_action_shape(self):
        if self.decision in {"confirm", "reject"} and not self.candidate_pack_ref:
            raise ValueError("candidate decisions require an exact pack reference")
        if self.decision == "confirm" and self.target_rule_id is not None:
            raise ValueError("candidate confirmation cannot target an active rule")
        if self.decision == "confirm" and (self.conditions or self.effects):
            raise ValueError(
                "candidate confirmation cannot carry unconsumed rule semantics"
            )
        if self.decision == "reject" and (
            self.enforcement is not None
            or self.target_rule_id is not None
            or self.conditions
            or self.effects
        ):
            raise ValueError("candidate rejection cannot carry rule semantics")
        if self.decision == "scoped_exception":
            if self.artifact_type != "rule" or not self.target_rule_id:
                raise ValueError("scoped exceptions require a target rule")
            if self.candidate_pack_ref is not None:
                raise ValueError("scoped exceptions do not target candidate packs")
            if not self.conditions:
                raise ValueError("scoped exceptions require explicit conditions")
        return self

    @property
    def action_hash(self) -> str:
        return canonical_hash(self)


class ConfirmationDecisionRecord(FrozenRuntimeModel):
    action_id: str = Field(min_length=1)
    action_hash: str = Field(min_length=1)
    decision: ConfirmationDecision
    artifact_type: ConfirmationArtifactType
    artifact_id: str = Field(min_length=1)
    candidate_pack_ref: str | None = None
    candidate_hash: str | None = None
    target_rule_id: str | None = None
    materialized_artifact_id: str | None = None
    scope: RuleScope
    rationale: str = Field(min_length=1)
    user_input_hash: str = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_CONFIRMATION_VERSION


class ConfirmationDecisionLedger(FrozenRuntimeModel):
    project_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    records: tuple[ConfirmationDecisionRecord, ...] = ()
    schema_version: str = WORLD_RUNTIME_CONFIRMATION_VERSION

    @model_validator(mode="after")
    def reject_duplicate_actions_or_candidate_decisions(self):
        action_ids = [record.action_id for record in self.records]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("decision ledger action IDs must be unique")
        candidate_keys = [
            (
                record.candidate_pack_ref,
                record.artifact_type,
                record.artifact_id,
            )
            for record in self.records
            if record.decision in {"confirm", "reject"}
        ]
        if len(candidate_keys) != len(set(candidate_keys)):
            raise ValueError("a candidate can have only one recorded decision")
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class ConstitutionChangeSet(FrozenRuntimeModel):
    change_set_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    base_constitution_version: str = Field(min_length=1)
    base_constitution_hash: str = Field(min_length=1)
    resulting_constitution_version: str = Field(min_length=1)
    added_rules: tuple[WorldRule, ...] = ()
    added_lifecycles: tuple[Lifecycle, ...] = ()
    decision_records: tuple[ConfirmationDecisionRecord, ...] = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_CONFIRMATION_VERSION

    @model_validator(mode="after")
    def reject_duplicate_added_artifacts(self):
        rule_ids = [rule.rule_id for rule in self.added_rules]
        lifecycle_ids = [item.lifecycle_id for item in self.added_lifecycles]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("change set rule IDs must be unique")
        if len(lifecycle_ids) != len(set(lifecycle_ids)):
            raise ValueError("change set lifecycle IDs must be unique")
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class ConfirmationApplyResult(FrozenRuntimeModel):
    change_set: ConstitutionChangeSet
    resulting_constitution: ProjectWorldConstitution
    decision_ledger: ConfirmationDecisionLedger
    schema_version: str = WORLD_RUNTIME_CONFIRMATION_VERSION


class DebugConflict(FrozenRuntimeModel):
    conflict_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    artifact_ids: tuple[str, ...]
    reason: str = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_CONFIRMATION_VERSION


class WorldRuntimeDebugView(FrozenRuntimeModel):
    project_id: str = Field(min_length=1)
    constitution_version: str = Field(min_length=1)
    frame_id: str = Field(min_length=1)
    frame_hash: str = Field(min_length=1)
    frame_status: str = Field(min_length=1)
    state_revision: int = Field(ge=0)
    active_rule_ids: tuple[str, ...]
    active_lifecycle_ids: tuple[str, ...]
    excluded_artifact_ids: tuple[str, ...]
    unknown_fact_ids: tuple[str, ...]
    conflicts: tuple[DebugConflict, ...]
    pending_confirmations: tuple[PendingConfirmationItem, ...]
    decision_ledger_hash: str | None = None
    schema_version: str = WORLD_RUNTIME_CONFIRMATION_VERSION

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)

    def render_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def render_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=True,
        )


def _pack_ref(pack: CandidatePack) -> str:
    return f"{pack.pack_id}@{pack.version}"


def _candidate_maps(
    packs: tuple[CandidatePack, ...],
) -> tuple[dict[tuple[str, str], tuple[CandidatePack, WorldRule]], dict[tuple[str, str], tuple[CandidatePack, Lifecycle]]]:
    rules: dict[tuple[str, str], tuple[CandidatePack, WorldRule]] = {}
    lifecycles: dict[tuple[str, str], tuple[CandidatePack, Lifecycle]] = {}
    for pack in packs:
        ref = _pack_ref(pack)
        for rule in pack.rules:
            key = (ref, rule.rule_id)
            if key in rules and canonical_hash(rules[key][1]) != canonical_hash(rule):
                raise ValueError("candidate rule ID resolves to multiple contents")
            rules[key] = (pack, rule)
        for lifecycle in pack.lifecycles:
            key = (ref, lifecycle.lifecycle_id)
            if key in lifecycles and canonical_hash(lifecycles[key][1]) != canonical_hash(lifecycle):
                raise ValueError("candidate lifecycle ID resolves to multiple contents")
            lifecycles[key] = (pack, lifecycle)
    return rules, lifecycles


def _priority(enforcement: str | None) -> ConfirmationPriority:
    return "high_impact" if enforcement == "block" else "provisional"


def build_world_runtime_debug_view(
    *,
    resolved: ResolvedWorldConstitution,
    frame: SceneRuntimeFrame,
    candidate_packs: tuple[CandidatePack, ...],
    decision_ledger: ConfirmationDecisionLedger | None = None,
) -> WorldRuntimeDebugView:
    """Build an on-demand debug projection; do not enumerate unrelated packs."""

    if resolved.project_id != frame.project_id:
        raise ValueError("resolved constitution and frame projects differ")
    if decision_ledger is not None and decision_ledger.project_id != frame.project_id:
        raise ValueError("decision ledger project differs from frame")
    rule_candidates, lifecycle_candidates = _candidate_maps(candidate_packs)
    rules_by_id: dict[str, list[tuple[CandidatePack, WorldRule]]] = {}
    for pack, rule in rule_candidates.values():
        rules_by_id.setdefault(rule.rule_id, []).append((pack, rule))
    lifecycles_by_id: dict[str, list[tuple[CandidatePack, Lifecycle]]] = {}
    for pack, lifecycle in lifecycle_candidates.values():
        lifecycles_by_id.setdefault(lifecycle.lifecycle_id, []).append(
            (pack, lifecycle)
        )
    decided = {
        (
            record.candidate_pack_ref,
            record.artifact_type,
            record.artifact_id,
        )
        for record in (decision_ledger.records if decision_ledger else ())
        if record.decision in {"confirm", "reject"}
    }
    active_rule_semantics = {rule.semantic_key for rule in resolved.active_rules}
    active_lifecycle_semantics = {
        lifecycle.semantic_key for lifecycle in resolved.active_lifecycles
    }

    pending: list[PendingConfirmationItem] = []
    for conflict in resolved.conflict_report.conflicts:
        pending.append(
            PendingConfirmationItem(
                item_id=f"pending:conflict:{conflict.conflict_id}",
                item_type="resolver_conflict",
                artifact_id=conflict.conflict_id,
                semantic_key=conflict.semantic_key,
                priority="blocking",
                reason=conflict.reason,
                source_hashes=conflict.source_hashes,
                allowed_actions=("resolve_manually", "add_scoped_exception"),
            )
        )
    for excluded in frame.excluded_artifacts:
        if excluded.exclusion_reason != "inactive_candidate":
            continue
        if excluded.artifact_type == "rule":
            matches = [
                found
                for found in rules_by_id.get(excluded.artifact_id, ())
                if found[1].provenance.source_hash == excluded.source_hash
            ]
            if len(matches) != 1:
                continue
            pack, candidate = matches[0]
            if candidate.semantic_key in active_rule_semantics:
                continue
            item_type = "candidate_rule"
            recommendation = candidate.activation_enforcement
        else:
            matches = [
                found
                for found in lifecycles_by_id.get(excluded.artifact_id, ())
                if found[1].provenance.source_hash == excluded.source_hash
            ]
            if len(matches) != 1:
                continue
            pack, candidate = matches[0]
            if candidate.semantic_key in active_lifecycle_semantics:
                continue
            item_type = "candidate_lifecycle"
            recommendation = candidate.activation_enforcement
        if (_pack_ref(pack), excluded.artifact_type, excluded.artifact_id) in decided:
            continue
        pending.append(
            PendingConfirmationItem(
                item_id=f"pending:{excluded.artifact_type}:{excluded.artifact_id}",
                item_type=item_type,
                artifact_id=excluded.artifact_id,
                semantic_key=candidate.semantic_key,
                priority=_priority(recommendation),
                recommended_enforcement=recommendation,
                reason=f"first relevant use from {_pack_ref(pack)} remains inactive",
                source_hashes=(pack.artifact_hash, canonical_hash(candidate)),
                allowed_actions=("confirm", "reject"),
            )
        )

    conflicts = tuple(
        DebugConflict(
            conflict_id=item.conflict_id,
            code=item.code,
            severity=item.severity,
            artifact_ids=item.artifact_ids,
            reason=item.reason,
        )
        for item in resolved.conflict_report.conflicts
    )
    return WorldRuntimeDebugView(
        project_id=frame.project_id,
        constitution_version=resolved.constitution_version,
        frame_id=frame.frame_id,
        frame_hash=frame.frame_hash,
        frame_status=frame.status,
        state_revision=frame.state_revision,
        active_rule_ids=tuple(sorted(item.rule_id for item in frame.activated_rules)),
        active_lifecycle_ids=tuple(
            sorted(item.lifecycle_id for item in frame.activated_lifecycles)
        ),
        excluded_artifact_ids=tuple(
            sorted(item.artifact_id for item in frame.excluded_artifacts)
        ),
        unknown_fact_ids=tuple(sorted(item.fact_id for item in frame.unknowns)),
        conflicts=tuple(sorted(conflicts, key=lambda item: item.conflict_id)),
        pending_confirmations=tuple(
            sorted(
                pending,
                key=lambda item: (
                    {"blocking": 0, "high_impact": 1, "provisional": 2}[
                        item.priority
                    ],
                    item.item_type,
                    item.artifact_id,
                ),
            )
        ),
        decision_ledger_hash=(
            decision_ledger.artifact_hash if decision_ledger else None
        ),
    )


def _scope_is_at_least_as_specific(scope: RuleScope, target: RuleScope) -> bool:
    def scalar(child, parent) -> bool:
        return parent is None or child == parent

    def members(child: tuple[str, ...], parent: tuple[str, ...]) -> bool:
        return not parent or (bool(child) and set(child).issubset(parent))

    return (
        scalar(scope.project_id, target.project_id)
        and members(scope.entity_ids, target.entity_ids)
        and members(scope.location_ids, target.location_ids)
        and members(scope.lifecycle_ids, target.lifecycle_ids)
        and scalar(scope.section, target.section)
        and scalar(scope.subsection, target.subsection)
    )


def _is_strict_scope(scope: RuleScope, target: RuleScope) -> bool:
    return _scope_is_at_least_as_specific(scope, target) and scope != target


def _confirmation_provenance(
    action: ConfirmationAction,
    *,
    candidate_hash: str | None,
    pack_ref: str | None,
) -> ProvenanceRef:
    body = {
        "action_hash": action.action_hash,
        "candidate_hash": candidate_hash,
        "pack_ref": pack_ref,
        "user_input_hash": action.user_input_hash,
    }
    return ProvenanceRef(
        source_id=f"user-action:{action.action_id}",
        source_type=f"user_{action.decision}",
        source_hash=canonical_hash(body),
        producer="apply_confirmation_actions",
    )


def _confirmed_rule(
    candidate: WorldRule,
    action: ConfirmationAction,
    pack_ref: str,
) -> WorldRule:
    candidate_hash = canonical_hash(candidate)
    materialized_id = (
        f"confirmed:{action.scope.project_id}:{candidate.rule_id}:"
        f"{action.action_hash[:12]}"
    )
    payload = candidate.model_dump()
    payload.update(
        rule_id=materialized_id,
        authority="user_override",
        enforcement=action.enforcement or candidate.activation_enforcement,
        activation_enforcement=None,
        scope=action.scope,
        provenance=_confirmation_provenance(
            action, candidate_hash=candidate_hash, pack_ref=pack_ref
        ),
        version="1",
    )
    return WorldRule(**payload)


def _replace_subject(subject: str, old_id: str, new_id: str) -> str:
    return f"${new_id}" if subject == f"${old_id}" else subject


def _confirmed_lifecycle(
    candidate: Lifecycle,
    action: ConfirmationAction,
    pack_ref: str,
) -> Lifecycle:
    candidate_hash = canonical_hash(candidate)
    new_id = (
        f"confirmed:{action.scope.project_id}:{candidate.lifecycle_id}:"
        f"{action.action_hash[:12]}"
    )
    transitions: list[LifecycleTransition] = []
    for transition in candidate.transitions:
        suffix = transition.transition_id.removeprefix(
            f"{candidate.lifecycle_id}."
        )
        transitions.append(
            LifecycleTransition(
                transition_id=f"{new_id}.{suffix}",
                from_state=transition.from_state,
                to_state=transition.to_state,
                guards=tuple(
                    predicate.model_copy(
                        update={
                            "subject": _replace_subject(
                                predicate.subject, candidate.lifecycle_id, new_id
                            )
                        }
                    )
                    for predicate in transition.guards
                ),
                effects=tuple(
                    effect.model_copy(
                        update={
                            "subject": _replace_subject(
                                effect.subject, candidate.lifecycle_id, new_id
                            )
                        }
                    )
                    for effect in transition.effects
                ),
                reversible=transition.reversible,
            )
        )
    payload = candidate.model_dump()
    payload.update(
        lifecycle_id=new_id,
        transitions=tuple(transitions),
        authority="user_override",
        enforcement=action.enforcement or candidate.activation_enforcement,
        activation_enforcement=None,
        scope=action.scope,
        provenance=_confirmation_provenance(
            action, candidate_hash=candidate_hash, pack_ref=pack_ref
        ),
        version="1",
    )
    return Lifecycle(**payload)


def _exception_rule(
    target: WorldRule, action: ConfirmationAction
) -> WorldRule:
    if not _is_strict_scope(action.scope, target.scope):
        raise ValueError("scoped exception must narrow its target rule scope")
    return WorldRule(
        rule_id=f"exception:{target.rule_id}:{action.action_hash[:12]}",
        semantic_key=f"{target.semantic_key}.exception",
        kind="permission",
        authority="user_override",
        enforcement=action.enforcement or target.enforcement,
        scope=action.scope,
        conditions=action.conditions,
        effects=action.effects,
        overrides_rule_ids=(target.rule_id,),
        provenance=_confirmation_provenance(
            action, candidate_hash=None, pack_ref=None
        ),
        version="1",
    )


def apply_confirmation_actions(
    *,
    constitution: ProjectWorldConstitution,
    candidate_packs: tuple[CandidatePack, ...],
    actions: tuple[ConfirmationAction, ...],
    prior_ledger: ConfirmationDecisionLedger | None = None,
) -> ConfirmationApplyResult:
    """Create a deterministic change set; no storage write occurs."""

    if not actions:
        raise ValueError("at least one explicit confirmation action is required")
    if prior_ledger is not None and prior_ledger.project_id != constitution.project_id:
        raise ValueError("decision ledger project differs from constitution")
    action_ids = [action.action_id for action in actions]
    if len(action_ids) != len(set(action_ids)):
        raise ValueError("confirmation action IDs must be unique")
    prior_records = prior_ledger.records if prior_ledger else ()
    prior_candidates = {
        (
            record.candidate_pack_ref,
            record.artifact_type,
            record.artifact_id,
        )
        for record in prior_records
        if record.decision in {"confirm", "reject"}
    }
    rules, lifecycles = _candidate_maps(candidate_packs)
    active_rules = {rule.rule_id: rule for rule in constitution.rules}
    added_rules: list[WorldRule] = []
    added_lifecycles: list[Lifecycle] = []
    records: list[ConfirmationDecisionRecord] = []

    for action in sorted(actions, key=lambda item: (item.action_id, item.action_hash)):
        if action.scope.project_id != constitution.project_id:
            raise ValueError("confirmation action scope must name the project")
        if action.decision in {"confirm", "reject"}:
            candidate_key = (
                action.candidate_pack_ref,
                action.artifact_type,
                action.artifact_id,
            )
            if candidate_key in prior_candidates:
                raise ValueError("candidate already has a recorded decision")
            if action.candidate_pack_ref not in constitution.bound_candidate_packs:
                raise ValueError("candidate action references an unbound pack version")
            lookup = rules if action.artifact_type == "rule" else lifecycles
            found = lookup.get((action.candidate_pack_ref, action.artifact_id))
            if found is None:
                raise ValueError("candidate artifact is absent from exact pack version")
            pack, candidate = found
            candidate_hash = canonical_hash(candidate)
            materialized_id = None
            if action.decision == "confirm":
                if isinstance(candidate, WorldRule):
                    artifact = _confirmed_rule(
                        candidate, action, action.candidate_pack_ref
                    )
                    added_rules.append(artifact)
                    materialized_id = artifact.rule_id
                else:
                    artifact = _confirmed_lifecycle(
                        candidate, action, action.candidate_pack_ref
                    )
                    added_lifecycles.append(artifact)
                    materialized_id = artifact.lifecycle_id
            records.append(
                ConfirmationDecisionRecord(
                    action_id=action.action_id,
                    action_hash=action.action_hash,
                    decision=action.decision,
                    artifact_type=action.artifact_type,
                    artifact_id=action.artifact_id,
                    candidate_pack_ref=_pack_ref(pack),
                    candidate_hash=candidate_hash,
                    materialized_artifact_id=materialized_id,
                    scope=action.scope,
                    rationale=action.rationale,
                    user_input_hash=action.user_input_hash,
                )
            )
        else:
            target = active_rules.get(action.target_rule_id)
            if target is None:
                raise ValueError("scoped exception target is absent from constitution")
            exception = _exception_rule(target, action)
            added_rules.append(exception)
            records.append(
                ConfirmationDecisionRecord(
                    action_id=action.action_id,
                    action_hash=action.action_hash,
                    decision=action.decision,
                    artifact_type="rule",
                    artifact_id=action.artifact_id,
                    target_rule_id=target.rule_id,
                    materialized_artifact_id=exception.rule_id,
                    scope=action.scope,
                    rationale=action.rationale,
                    user_input_hash=action.user_input_hash,
                )
            )

    change_body = {
        "project_id": constitution.project_id,
        "base_hash": constitution.artifact_hash,
        "action_hashes": tuple(sorted(action.action_hash for action in actions)),
    }
    change_digest = canonical_hash(change_body)
    result_version = f"{constitution.version}+wr0g.{change_digest[:12]}"
    change_set = ConstitutionChangeSet(
        change_set_id=f"constitution-change:{change_digest[:24]}",
        project_id=constitution.project_id,
        base_constitution_version=constitution.version,
        base_constitution_hash=constitution.artifact_hash,
        resulting_constitution_version=result_version,
        added_rules=tuple(sorted(added_rules, key=lambda item: item.rule_id)),
        added_lifecycles=tuple(
            sorted(added_lifecycles, key=lambda item: item.lifecycle_id)
        ),
        decision_records=tuple(sorted(records, key=lambda item: item.action_id)),
    )
    resulting = replay_constitution_change(
        constitution=constitution, change_set=change_set
    )
    all_records = tuple(
        sorted((*prior_records, *records), key=lambda item: item.action_id)
    )
    ledger = ConfirmationDecisionLedger(
        project_id=constitution.project_id,
        version=f"decision-ledger:{canonical_hash(all_records)[:16]}",
        records=all_records,
    )
    return ConfirmationApplyResult(
        change_set=change_set,
        resulting_constitution=resulting,
        decision_ledger=ledger,
    )


def replay_constitution_change(
    *,
    constitution: ProjectWorldConstitution,
    change_set: ConstitutionChangeSet,
) -> ProjectWorldConstitution:
    """Replay a recorded change set against its exact base constitution."""

    if change_set.project_id != constitution.project_id:
        raise ValueError("change set project differs from constitution")
    if change_set.base_constitution_version != constitution.version:
        raise ValueError("change set base version mismatch")
    if change_set.base_constitution_hash != constitution.artifact_hash:
        raise ValueError("change set base hash mismatch")
    existing_rule_ids = {rule.rule_id for rule in constitution.rules}
    existing_lifecycle_ids = {
        lifecycle.lifecycle_id for lifecycle in constitution.lifecycles
    }
    if existing_rule_ids & {rule.rule_id for rule in change_set.added_rules}:
        raise ValueError("change set would duplicate a constitution rule ID")
    if existing_lifecycle_ids & {
        lifecycle.lifecycle_id for lifecycle in change_set.added_lifecycles
    }:
        raise ValueError("change set would duplicate a constitution lifecycle ID")
    return ProjectWorldConstitution(
        project_id=constitution.project_id,
        version=change_set.resulting_constitution_version,
        rules=tuple(
            sorted(
                (*constitution.rules, *change_set.added_rules),
                key=lambda item: item.rule_id,
            )
        ),
        lifecycles=tuple(
            sorted(
                (*constitution.lifecycles, *change_set.added_lifecycles),
                key=lambda item: item.lifecycle_id,
            )
        ),
        bound_candidate_packs=constitution.bound_candidate_packs,
    )
