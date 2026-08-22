"""WR0-B deterministic resolution of world-runtime rules and candidates.

This module is deliberately offline.  It resolves immutable artifacts and
returns an explainable result; it does not read storage, call an LLM, compile a
scene RuntimeFrame, or mutate production state.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Literal, TypeVar

from pydantic import Field, model_validator

from .world_runtime_contracts import (
    CandidatePack,
    FrozenRuntimeModel,
    Lifecycle,
    ProjectWorldConstitution,
    RuleScope,
    WorldRule,
    canonical_hash,
)
from .world_runtime_kernel import (
    UniversalRuntimeKernel,
    validate_minimal_universal_kernel,
)


WORLD_RUNTIME_RESOLVER_VERSION = "world-runtime-resolver-wr0b-v1"

ConflictCode = Literal[
    "missing_bound_pack_version",
    "duplicate_pack_version",
    "project_mismatch",
    "same_layer_rule_conflict",
    "same_layer_lifecycle_conflict",
    "overlapping_scope_conflict",
    "missing_override_target",
    "ambiguous_override_target",
    "override_scope_expansion",
    "insufficient_override_authority",
    "invalid_universal_kernel",
]
ResolutionAction = Literal[
    "selected",
    "shadowed",
    "inactive_candidate",
    "ignored_unbound_pack",
    "scope_precedence",
    "valid_exception",
    "rejected_conflict",
]


_AUTHORITY_RANK = {
    "pack_candidate": 0,
    "model_inferred": 10,
    "text_extracted": 20,
    "kernel": 30,
    "project_explicit": 40,
    "user_override": 50,
}


class UserOverrideSet(FrozenRuntimeModel):
    project_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    rules: tuple[WorldRule, ...] = ()
    lifecycles: tuple[Lifecycle, ...] = ()
    schema_version: str = WORLD_RUNTIME_RESOLVER_VERSION

    @model_validator(mode="after")
    def allow_user_overrides_only(self):
        if any(item.authority != "user_override" for item in self.rules):
            raise ValueError("override rules require user_override authority")
        if any(item.authority != "user_override" for item in self.lifecycles):
            raise ValueError("override lifecycles require user_override authority")
        return self


class ResolutionConflict(FrozenRuntimeModel):
    conflict_id: str = Field(min_length=1)
    code: ConflictCode
    severity: Literal["blocking", "warning"]
    artifact_ids: tuple[str, ...]
    semantic_key: str | None = None
    reason: str = Field(min_length=1)
    source_hashes: tuple[str, ...] = ()
    schema_version: str = WORLD_RUNTIME_RESOLVER_VERSION


class ConflictReport(FrozenRuntimeModel):
    project_id: str = Field(min_length=1)
    conflicts: tuple[ResolutionConflict, ...] = ()
    schema_version: str = WORLD_RUNTIME_RESOLVER_VERSION

    @property
    def has_blocking(self) -> bool:
        return any(item.severity == "blocking" for item in self.conflicts)

    @property
    def report_hash(self) -> str:
        return canonical_hash(self)


class ResolutionDecision(FrozenRuntimeModel):
    artifact_type: Literal["rule", "lifecycle", "candidate_pack"]
    artifact_id: str = Field(min_length=1)
    action: ResolutionAction
    reason: str = Field(min_length=1)
    related_artifact_ids: tuple[str, ...] = ()
    schema_version: str = WORLD_RUNTIME_RESOLVER_VERSION


class ResolvedWorldConstitution(FrozenRuntimeModel):
    project_id: str = Field(min_length=1)
    constitution_version: str = Field(min_length=1)
    override_version: str | None = None
    kernel_version: str | None = None
    kernel_hash: str | None = None
    bound_candidate_packs: tuple[str, ...] = ()
    active_rules: tuple[WorldRule, ...] = ()
    active_lifecycles: tuple[Lifecycle, ...] = ()
    inactive_candidate_rules: tuple[WorldRule, ...] = ()
    inactive_candidate_lifecycles: tuple[Lifecycle, ...] = ()
    decisions: tuple[ResolutionDecision, ...] = ()
    conflict_report: ConflictReport
    schema_version: str = WORLD_RUNTIME_RESOLVER_VERSION

    @property
    def resolved_hash(self) -> str:
        return canonical_hash(self)


def _pack_ref(pack: CandidatePack) -> str:
    return f"{pack.pack_id}@{pack.version}"


def _scope_key(scope: RuleScope) -> str:
    return canonical_hash(scope)


def _scope_specificity(scope: RuleScope) -> tuple[int, ...]:
    return (
        int(scope.subsection is not None),
        int(scope.section is not None),
        int(scope.project_id is not None),
        int(bool(scope.entity_ids)),
        -len(scope.entity_ids),
        int(bool(scope.location_ids)),
        -len(scope.location_ids),
        int(bool(scope.lifecycle_ids)),
        -len(scope.lifecycle_ids),
    )


def _scalar_overlaps(left, right) -> bool:
    return left is None or right is None or left == right


def _set_overlaps(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return not left or not right or bool(set(left) & set(right))


def _scopes_overlap(left: RuleScope, right: RuleScope) -> bool:
    return (
        _scalar_overlaps(left.project_id, right.project_id)
        and _set_overlaps(left.entity_ids, right.entity_ids)
        and _set_overlaps(left.location_ids, right.location_ids)
        and _set_overlaps(left.lifecycle_ids, right.lifecycle_ids)
        and _scalar_overlaps(left.section, right.section)
        and _scalar_overlaps(left.subsection, right.subsection)
    )


def _scalar_is_subset(more_specific, less_specific) -> bool:
    return less_specific is None or more_specific == less_specific


def _set_is_subset(
    more_specific: tuple[str, ...], less_specific: tuple[str, ...]
) -> bool:
    if not less_specific:
        return True
    return bool(more_specific) and set(more_specific).issubset(less_specific)


def _scope_is_at_least_as_specific(
    more_specific: RuleScope, less_specific: RuleScope
) -> bool:
    return (
        _scalar_is_subset(more_specific.project_id, less_specific.project_id)
        and _set_is_subset(more_specific.entity_ids, less_specific.entity_ids)
        and _set_is_subset(more_specific.location_ids, less_specific.location_ids)
        and _set_is_subset(more_specific.lifecycle_ids, less_specific.lifecycle_ids)
        and _scalar_is_subset(more_specific.section, less_specific.section)
        and _scalar_is_subset(more_specific.subsection, less_specific.subsection)
    )


def _rule_semantics(rule: WorldRule) -> str:
    return canonical_hash(
        {
            "semantic_key": rule.semantic_key,
            "kind": rule.kind,
            "enforcement": rule.enforcement,
            "activation_enforcement": rule.activation_enforcement,
            "conditions": rule.conditions,
            "prerequisites": rule.prerequisites,
            "effects": rule.effects,
            "overrides_rule_ids": rule.overrides_rule_ids,
            "valid_time": rule.valid_time,
        }
    )


def _lifecycle_semantics(lifecycle: Lifecycle) -> str:
    return canonical_hash(
        {
            "semantic_key": lifecycle.semantic_key,
            "states": lifecycle.states,
            "initial_state": lifecycle.initial_state,
            "transitions": lifecycle.transitions,
            "terminal_states": lifecycle.terminal_states,
            "enforcement": lifecycle.enforcement,
            "activation_enforcement": lifecycle.activation_enforcement,
            "valid_time": lifecycle.valid_time,
        }
    )


def _artifact_sort_key(item: WorldRule | Lifecycle) -> tuple:
    item_id = item.rule_id if isinstance(item, WorldRule) else item.lifecycle_id
    return (
        item.semantic_key,
        tuple(-value for value in _scope_specificity(item.scope)),
        -_AUTHORITY_RANK[item.authority],
        item_id,
        canonical_hash(item),
    )


def _conflict(
    *,
    code: ConflictCode,
    artifact_ids: Iterable[str],
    reason: str,
    semantic_key: str | None = None,
    source_hashes: Iterable[str] = (),
) -> ResolutionConflict:
    ids = tuple(sorted(set(artifact_ids)))
    hashes = tuple(sorted(set(source_hashes)))
    body = {
        "code": code,
        "artifact_ids": ids,
        "semantic_key": semantic_key,
        "reason": reason,
        "source_hashes": hashes,
    }
    return ResolutionConflict(
        conflict_id=f"runtime-conflict:{canonical_hash(body)[:24]}",
        code=code,
        severity="blocking",
        artifact_ids=ids,
        semantic_key=semantic_key,
        reason=reason,
        source_hashes=hashes,
    )


T = TypeVar("T", WorldRule, Lifecycle)


def _resolve_active_items(
    items: Iterable[T],
    *,
    artifact_type: Literal["rule", "lifecycle"],
) -> tuple[list[T], list[ResolutionDecision], list[ResolutionConflict]]:
    id_attr = "rule_id" if artifact_type == "rule" else "lifecycle_id"
    semantics = _rule_semantics if artifact_type == "rule" else _lifecycle_semantics
    conflict_code: ConflictCode = (
        "same_layer_rule_conflict"
        if artifact_type == "rule"
        else "same_layer_lifecycle_conflict"
    )
    grouped: dict[tuple[str, str], list[T]] = defaultdict(list)
    for item in items:
        grouped[(item.semantic_key, _scope_key(item.scope))].append(item)

    selected: list[T] = []
    decisions: list[ResolutionDecision] = []
    conflicts: list[ResolutionConflict] = []
    for (semantic_key, _), group in sorted(grouped.items()):
        ordered = sorted(group, key=_artifact_sort_key)
        highest_rank = max(_AUTHORITY_RANK[item.authority] for item in ordered)
        highest = [
            item for item in ordered if _AUTHORITY_RANK[item.authority] == highest_rank
        ]
        semantic_hashes = {semantics(item) for item in highest}
        if len(semantic_hashes) > 1:
            ids = [getattr(item, id_attr) for item in highest]
            conflicts.append(
                _conflict(
                    code=conflict_code,
                    artifact_ids=ids,
                    semantic_key=semantic_key,
                    reason="same authority and scope define incompatible semantics",
                    source_hashes=(item.provenance.source_hash for item in highest),
                )
            )
            decisions.extend(
                ResolutionDecision(
                    artifact_type=artifact_type,
                    artifact_id=getattr(item, id_attr),
                    action="rejected_conflict",
                    reason="same_layer_conflict",
                    related_artifact_ids=tuple(sorted(ids)),
                )
                for item in highest
            )
            continue

        winner = min(highest, key=lambda item: canonical_hash(item))
        winner_id = getattr(winner, id_attr)
        selected.append(winner)
        decisions.append(
            ResolutionDecision(
                artifact_type=artifact_type,
                artifact_id=winner_id,
                action="selected",
                reason="highest_authority_for_exact_scope",
            )
        )
        for item in ordered:
            if item is winner:
                continue
            decisions.append(
                ResolutionDecision(
                    artifact_type=artifact_type,
                    artifact_id=getattr(item, id_attr),
                    action="shadowed",
                    reason="lower_authority_or_duplicate_for_exact_scope",
                    related_artifact_ids=(winner_id,),
                )
            )

    selected.sort(key=_artifact_sort_key)
    for index, left in enumerate(selected):
        for right in selected[index + 1 :]:
            if left.semantic_key != right.semantic_key:
                continue
            if not _scopes_overlap(left.scope, right.scope):
                continue
            left_specific = _scope_is_at_least_as_specific(left.scope, right.scope)
            right_specific = _scope_is_at_least_as_specific(right.scope, left.scope)
            if left_specific or right_specific:
                specific = left if left_specific and not right_specific else right
                general = right if specific is left else left
                decisions.append(
                    ResolutionDecision(
                        artifact_type=artifact_type,
                        artifact_id=getattr(specific, id_attr),
                        action="scope_precedence",
                        reason="more_specific_scope_precedes_overlapping_general_scope",
                        related_artifact_ids=(getattr(general, id_attr),),
                    )
                )
                continue
            if semantics(left) != semantics(right):
                conflicts.append(
                    _conflict(
                        code="overlapping_scope_conflict",
                        artifact_ids=(getattr(left, id_attr), getattr(right, id_attr)),
                        semantic_key=left.semantic_key,
                        reason="overlapping incomparable scopes define incompatible semantics",
                        source_hashes=(
                            left.provenance.source_hash,
                            right.provenance.source_hash,
                        ),
                    )
                )

    return selected, decisions, conflicts


def _validate_rule_exceptions(
    rules: list[WorldRule],
) -> tuple[list[WorldRule], list[ResolutionDecision], list[ResolutionConflict]]:
    by_id: dict[str, list[WorldRule]] = defaultdict(list)
    for rule in rules:
        by_id[rule.rule_id].append(rule)
    rejected_hashes: set[str] = set()
    decisions: list[ResolutionDecision] = []
    conflicts: list[ResolutionConflict] = []
    for rule in rules:
        for target_id in rule.overrides_rule_ids:
            targets = by_id.get(target_id, [])
            if not targets:
                conflicts.append(
                    _conflict(
                        code="missing_override_target",
                        artifact_ids=(rule.rule_id, target_id),
                        semantic_key=rule.semantic_key,
                        reason="exception references a rule that is not active",
                        source_hashes=(rule.provenance.source_hash,),
                    )
                )
                rejected_hashes.add(canonical_hash(rule))
                continue
            if len(targets) > 1:
                conflicts.append(
                    _conflict(
                        code="ambiguous_override_target",
                        artifact_ids=(rule.rule_id, target_id),
                        semantic_key=rule.semantic_key,
                        reason="exception target resolves to multiple active scopes",
                        source_hashes=(rule.provenance.source_hash,),
                    )
                )
                rejected_hashes.add(canonical_hash(rule))
                continue
            target = targets[0]
            if not _scope_is_at_least_as_specific(rule.scope, target.scope):
                conflicts.append(
                    _conflict(
                        code="override_scope_expansion",
                        artifact_ids=(rule.rule_id, target.rule_id),
                        semantic_key=rule.semantic_key,
                        reason="exception scope is broader than its target rule scope",
                        source_hashes=(
                            rule.provenance.source_hash,
                            target.provenance.source_hash,
                        ),
                    )
                )
                rejected_hashes.add(canonical_hash(rule))
                continue
            if _AUTHORITY_RANK[rule.authority] < _AUTHORITY_RANK[target.authority]:
                conflicts.append(
                    _conflict(
                        code="insufficient_override_authority",
                        artifact_ids=(rule.rule_id, target.rule_id),
                        semantic_key=rule.semantic_key,
                        reason="exception authority is lower than target rule authority",
                        source_hashes=(
                            rule.provenance.source_hash,
                            target.provenance.source_hash,
                        ),
                    )
                )
                rejected_hashes.add(canonical_hash(rule))
                continue
            decisions.append(
                ResolutionDecision(
                    artifact_type="rule",
                    artifact_id=rule.rule_id,
                    action="valid_exception",
                    reason="explicit_target_scope_and_authority_validated",
                    related_artifact_ids=(target.rule_id,),
                )
            )
    rejected_ids = {
        rule.rule_id for rule in rules if canonical_hash(rule) in rejected_hashes
    }
    decisions = [
        item for item in decisions if item.artifact_id not in rejected_ids
    ]
    decisions.extend(
        ResolutionDecision(
            artifact_type="rule",
            artifact_id=rule_id,
            action="rejected_conflict",
            reason="invalid_scoped_exception",
        )
        for rule_id in sorted(rejected_ids)
    )
    return (
        [rule for rule in rules if canonical_hash(rule) not in rejected_hashes],
        decisions,
        conflicts,
    )


class WorldRuntimeResolver:
    """Resolve WR0 artifacts deterministically without activating candidates."""

    def resolve(
        self,
        *,
        constitution: ProjectWorldConstitution,
        candidate_packs: Iterable[CandidatePack] = (),
        user_overrides: UserOverrideSet | None = None,
        kernel: UniversalRuntimeKernel | None = None,
    ) -> ResolvedWorldConstitution:
        conflicts: list[ResolutionConflict] = []
        decisions: list[ResolutionDecision] = []
        kernel_rules: tuple[WorldRule, ...] = ()
        kernel_version = None
        kernel_hash = None
        if kernel is not None:
            kernel_version = kernel.version
            kernel_hash = kernel.artifact_hash
            kernel_report = validate_minimal_universal_kernel(kernel)
            if kernel_report.valid:
                kernel_rules = kernel.rules
            else:
                conflicts.append(
                    _conflict(
                        code="invalid_universal_kernel",
                        artifact_ids=tuple(
                            sorted(
                                {
                                    rule_id
                                    for issue in kernel_report.issues
                                    for rule_id in issue.rule_ids
                                }
                            )
                        )
                        or (kernel.kernel_id,),
                        reason="universal kernel failed its frozen integrity gate",
                        source_hashes=(kernel_report.report_hash,),
                    )
                )
        if user_overrides is not None and user_overrides.project_id != constitution.project_id:
            conflicts.append(
                _conflict(
                    code="project_mismatch",
                    artifact_ids=(constitution.project_id, user_overrides.project_id),
                    reason="override project does not match constitution project",
                )
            )
            override_rules: tuple[WorldRule, ...] = ()
            override_lifecycles: tuple[Lifecycle, ...] = ()
        else:
            override_rules = user_overrides.rules if user_overrides else ()
            override_lifecycles = user_overrides.lifecycles if user_overrides else ()

        packs_by_ref: dict[str, list[CandidatePack]] = defaultdict(list)
        for pack in candidate_packs:
            packs_by_ref[_pack_ref(pack)].append(pack)

        bound_refs = tuple(sorted(set(constitution.bound_candidate_packs)))
        bound_packs: list[CandidatePack] = []
        for ref in bound_refs:
            matches = packs_by_ref.get(ref, [])
            if not matches:
                conflicts.append(
                    _conflict(
                        code="missing_bound_pack_version",
                        artifact_ids=(ref,),
                        reason="project-bound candidate pack version is unavailable",
                    )
                )
                continue
            hashes = {pack.artifact_hash for pack in matches}
            if len(hashes) > 1:
                conflicts.append(
                    _conflict(
                        code="duplicate_pack_version",
                        artifact_ids=(ref,),
                        reason="same pack ID/version has multiple contents",
                        source_hashes=hashes,
                    )
                )
                continue
            bound_packs.append(min(matches, key=lambda pack: pack.artifact_hash))

        for ref, packs in sorted(packs_by_ref.items()):
            if ref in bound_refs:
                continue
            for _ in packs:
                decisions.append(
                    ResolutionDecision(
                        artifact_type="candidate_pack",
                        artifact_id=ref,
                        action="ignored_unbound_pack",
                        reason="candidate pack is not bound to this project version",
                    )
                )

        inactive_rules = sorted(
            (rule for pack in bound_packs for rule in pack.rules),
            key=_artifact_sort_key,
        )
        inactive_lifecycles = sorted(
            (lifecycle for pack in bound_packs for lifecycle in pack.lifecycles),
            key=_artifact_sort_key,
        )
        decisions.extend(
            ResolutionDecision(
                artifact_type="rule",
                artifact_id=rule.rule_id,
                action="inactive_candidate",
                reason="pack candidates require explicit project confirmation",
            )
            for rule in inactive_rules
        )
        decisions.extend(
            ResolutionDecision(
                artifact_type="lifecycle",
                artifact_id=lifecycle.lifecycle_id,
                action="inactive_candidate",
                reason="pack candidates require explicit project confirmation",
            )
            for lifecycle in inactive_lifecycles
        )

        active_rules, rule_decisions, rule_conflicts = _resolve_active_items(
            (*kernel_rules, *constitution.rules, *override_rules),
            artifact_type="rule",
        )
        active_lifecycles, lifecycle_decisions, lifecycle_conflicts = (
            _resolve_active_items(
                (*constitution.lifecycles, *override_lifecycles),
                artifact_type="lifecycle",
            )
        )
        decisions.extend(rule_decisions)
        decisions.extend(lifecycle_decisions)
        conflicts.extend(rule_conflicts)
        conflicts.extend(lifecycle_conflicts)

        active_rules, exception_decisions, exception_conflicts = (
            _validate_rule_exceptions(active_rules)
        )
        rejected_exception_ids = {
            item.artifact_id
            for item in exception_decisions
            if item.action == "rejected_conflict"
        }
        decisions = [
            item
            for item in decisions
            if not (
                item.artifact_type == "rule"
                and item.artifact_id in rejected_exception_ids
                and item.action in {"selected", "scope_precedence"}
            )
        ]
        decisions.extend(exception_decisions)
        conflicts.extend(exception_conflicts)

        decisions = sorted(
            decisions,
            key=lambda item: (
                item.artifact_type,
                item.artifact_id,
                item.action,
                item.reason,
                item.related_artifact_ids,
            ),
        )
        conflicts = sorted(
            {item.conflict_id: item for item in conflicts}.values(),
            key=lambda item: item.conflict_id,
        )
        report = ConflictReport(
            project_id=constitution.project_id,
            conflicts=tuple(conflicts),
        )
        return ResolvedWorldConstitution(
            project_id=constitution.project_id,
            constitution_version=constitution.version,
            override_version=user_overrides.version if user_overrides else None,
            kernel_version=kernel_version,
            kernel_hash=kernel_hash,
            bound_candidate_packs=bound_refs,
            active_rules=tuple(sorted(active_rules, key=_artifact_sort_key)),
            active_lifecycles=tuple(
                sorted(active_lifecycles, key=_artifact_sort_key)
            ),
            inactive_candidate_rules=tuple(inactive_rules),
            inactive_candidate_lifecycles=tuple(inactive_lifecycles),
            decisions=tuple(decisions),
            conflict_report=report,
        )
