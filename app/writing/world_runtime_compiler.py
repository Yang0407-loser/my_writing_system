"""WR0-F deterministic, read-only compilation of scene runtime frames.

The compiler consumes already-resolved rules, canonical state, and typed event
bindings.  It performs relevance projection and lifecycle path planning only;
it does not render a Writer prompt, validate generated prose, or commit state.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Literal

from pydantic import Field, model_validator

from .world_runtime_contracts import (
    CanonicalWorldState,
    FrozenRuntimeModel,
    Lifecycle,
    LifecycleTransition,
    RuleScope,
    StatePredicate,
    WorldFact,
    WorldRule,
    canonical_hash,
)
from .world_runtime_event_contracts import SubsectionEventContract
from .world_runtime_resolver import ResolvedWorldConstitution


WORLD_RUNTIME_COMPILER_VERSION = "world-runtime-compiler-wr0f-v1"

FrameStatus = Literal["complete", "partial", "blocked"]
ExclusionReason = Literal["inactive_candidate", "scope_mismatch"]
TransitionAvailability = Literal["currently_applicable", "requires_prior_transition"]
EventBoundaryStatus = Literal["ready", "requires_bridge", "unresolved"]
CompileIssueCode = Literal[
    "blocking_resolution_conflict",
    "project_mismatch",
    "missing_bound_fact",
    "missing_bound_lifecycle",
    "missing_required_transition",
    "unbridgeable_must_event",
]


class RuntimeFactProjection(FrozenRuntimeModel):
    fact_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    value: Any = None
    epistemic_status: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_COMPILER_VERSION


class ActivatedRuntimeRule(FrozenRuntimeModel):
    rule_id: str = Field(min_length=1)
    semantic_key: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    enforcement: Literal["block", "warn", "suggest"]
    activation_reason: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_COMPILER_VERSION


class ExcludedRuntimeArtifact(FrozenRuntimeModel):
    artifact_type: Literal["rule", "lifecycle"]
    artifact_id: str = Field(min_length=1)
    semantic_key: str = Field(min_length=1)
    exclusion_reason: ExclusionReason
    source_id: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_COMPILER_VERSION


class ActivatedRuntimeLifecycle(FrozenRuntimeModel):
    lifecycle_id: str = Field(min_length=1)
    semantic_key: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    enforcement: Literal["block", "warn", "suggest"]
    current_state_fact_id: str = Field(min_length=1)
    current_state: str = Field(min_length=1)
    activation_reason: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_COMPILER_VERSION


class RuntimeTransitionOption(FrozenRuntimeModel):
    event_id: str = Field(min_length=1)
    lifecycle_id: str = Field(min_length=1)
    transition_id: str = Field(min_length=1)
    from_state: str = Field(min_length=1)
    to_state: str = Field(min_length=1)
    availability: TransitionAvailability
    required_by_event: bool
    preceding_transition_ids: tuple[str, ...] = ()
    guards: tuple[StatePredicate, ...] = ()
    schema_version: str = WORLD_RUNTIME_COMPILER_VERSION


class RuntimeUnknown(FrozenRuntimeModel):
    fact_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_COMPILER_VERSION


class CompiledEventBoundary(FrozenRuntimeModel):
    event_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool
    relevant_fact_ids: tuple[str, ...]
    transition_ids: tuple[str, ...] = ()
    bridge_transition_ids: tuple[str, ...] = ()
    status: EventBoundaryStatus
    schema_version: str = WORLD_RUNTIME_COMPILER_VERSION


class RuntimeCompileIssue(FrozenRuntimeModel):
    issue_id: str = Field(min_length=1)
    code: CompileIssueCode
    artifact_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_COMPILER_VERSION


class SceneRuntimeFrame(FrozenRuntimeModel):
    frame_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    section: int = Field(ge=1)
    subsection: int = Field(ge=1)
    state_revision: int = Field(ge=0)
    constitution_version: str = Field(min_length=1)
    kernel_version: str | None = None
    event_contract_id: str = Field(min_length=1)
    event_contract_hash: str = Field(min_length=1)
    relevant_source_hash: str = Field(min_length=1)
    status: FrameStatus
    facts: tuple[RuntimeFactProjection, ...] = ()
    activated_rules: tuple[ActivatedRuntimeRule, ...] = ()
    activated_lifecycles: tuple[ActivatedRuntimeLifecycle, ...] = ()
    excluded_artifacts: tuple[ExcludedRuntimeArtifact, ...] = ()
    transition_options: tuple[RuntimeTransitionOption, ...] = ()
    event_boundaries: tuple[CompiledEventBoundary, ...] = ()
    unknowns: tuple[RuntimeUnknown, ...] = ()
    issues: tuple[RuntimeCompileIssue, ...] = ()
    schema_version: str = WORLD_RUNTIME_COMPILER_VERSION

    @model_validator(mode="after")
    def validate_projection(self):
        collections = (
            ("facts", [item.fact_id for item in self.facts]),
            ("activated rules", [item.rule_id for item in self.activated_rules]),
            (
                "activated lifecycles",
                [item.lifecycle_id for item in self.activated_lifecycles],
            ),
            (
                "excluded artifacts",
                [
                    (item.artifact_type, item.artifact_id)
                    for item in self.excluded_artifacts
                ],
            ),
            (
                "transition options",
                [
                    (item.event_id, item.transition_id)
                    for item in self.transition_options
                ],
            ),
            ("event boundaries", [item.event_id for item in self.event_boundaries]),
            ("unknowns", [item.fact_id for item in self.unknowns]),
            ("issues", [item.issue_id for item in self.issues]),
        )
        for label, ids in collections:
            if len(ids) != len(set(ids)):
                raise ValueError(f"runtime frame has duplicate {label}")
        active_ids = {item.rule_id for item in self.activated_rules}
        excluded_rule_ids = {
            item.artifact_id
            for item in self.excluded_artifacts
            if item.artifact_type == "rule"
        }
        if active_ids & excluded_rule_ids:
            raise ValueError("a rule cannot be both activated and excluded")
        if self.status == "blocked" and (
            self.activated_rules or self.activated_lifecycles or self.transition_options
        ):
            raise ValueError("blocked frame cannot expose executable artifacts")
        return self

    @property
    def frame_hash(self) -> str:
        return canonical_hash(self)


def _issue(
    code: CompileIssueCode, *, artifact_ids: tuple[str, ...], reason: str
) -> RuntimeCompileIssue:
    body = {
        "code": code,
        "artifact_ids": tuple(sorted(set(artifact_ids))),
        "reason": reason,
    }
    return RuntimeCompileIssue(
        issue_id=f"runtime-compile-issue:{canonical_hash(body)[:24]}",
        code=code,
        artifact_ids=body["artifact_ids"],
        reason=reason,
    )


def _domain(semantic_key: str) -> str:
    return semantic_key.split(".", 1)[0]


def _scope_applies(
    scope: RuleScope,
    *,
    project_id: str,
    section: int,
    subsection: int,
    relevant_subjects: set[str],
    relevant_lifecycle_ids: set[str],
) -> bool:
    if scope.project_id is not None and scope.project_id != project_id:
        return False
    if scope.section is not None and scope.section != section:
        return False
    if scope.subsection is not None and scope.subsection != subsection:
        return False
    if scope.entity_ids and not set(scope.entity_ids) & relevant_subjects:
        return False
    if scope.location_ids and not set(scope.location_ids) & relevant_subjects:
        return False
    if scope.lifecycle_ids and not set(scope.lifecycle_ids) & relevant_lifecycle_ids:
        return False
    return True


def _fact_projection(fact: WorldFact) -> RuntimeFactProjection:
    return RuntimeFactProjection(
        fact_id=fact.fact_id,
        subject=fact.subject,
        predicate=fact.predicate,
        value=fact.value,
        epistemic_status=fact.epistemic_status,
        source_id=fact.provenance.source_id,
        source_hash=fact.provenance.source_hash,
    )


def _activated_rule(rule: WorldRule, reason: str) -> ActivatedRuntimeRule:
    if rule.enforcement == "inactive":
        raise ValueError("inactive rule cannot be activated")
    return ActivatedRuntimeRule(
        rule_id=rule.rule_id,
        semantic_key=rule.semantic_key,
        kind=rule.kind,
        authority=rule.authority,
        enforcement=rule.enforcement,
        activation_reason=reason,
        source_id=rule.provenance.source_id,
        source_hash=rule.provenance.source_hash,
    )


def _excluded(
    item: WorldRule | Lifecycle,
    *,
    artifact_type: Literal["rule", "lifecycle"],
    reason: ExclusionReason,
) -> ExcludedRuntimeArtifact:
    artifact_id = item.rule_id if isinstance(item, WorldRule) else item.lifecycle_id
    return ExcludedRuntimeArtifact(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        semantic_key=item.semantic_key,
        exclusion_reason=reason,
        source_id=item.provenance.source_id,
        source_hash=item.provenance.source_hash,
    )


def _shortest_path(
    lifecycle: Lifecycle, start: str, target: str
) -> tuple[LifecycleTransition, ...] | None:
    if start == target:
        return ()
    outgoing: dict[str, list[LifecycleTransition]] = {}
    for transition in lifecycle.transitions:
        outgoing.setdefault(transition.from_state, []).append(transition)
    queue = deque([(start, ())])
    visited = {start}
    while queue:
        state, path = queue.popleft()
        for transition in sorted(
            outgoing.get(state, ()), key=lambda item: item.transition_id
        ):
            next_path = (*path, transition)
            if transition.to_state == target:
                return next_path
            if transition.to_state not in visited:
                visited.add(transition.to_state)
                queue.append((transition.to_state, next_path))
    return None


class WorldRuntimeCompiler:
    """Compile a small scene projection without mutating any input artifact."""

    def compile(
        self,
        *,
        resolved: ResolvedWorldConstitution,
        state_before: CanonicalWorldState,
        event_contract: SubsectionEventContract,
    ) -> SceneRuntimeFrame:
        project_ids = {
            resolved.project_id,
            state_before.project_id,
            event_contract.project_id,
        }
        preflight_issues: list[RuntimeCompileIssue] = []
        if len(project_ids) != 1:
            preflight_issues.append(
                _issue(
                    "project_mismatch",
                    artifact_ids=tuple(sorted(project_ids)),
                    reason="resolved constitution, state and event contract projects differ",
                )
            )
        if resolved.conflict_report.has_blocking:
            preflight_issues.append(
                _issue(
                    "blocking_resolution_conflict",
                    artifact_ids=tuple(
                        conflict.conflict_id
                        for conflict in resolved.conflict_report.conflicts
                        if conflict.severity == "blocking"
                    ),
                    reason="runtime compilation is gated by resolver conflicts",
                )
            )
        if preflight_issues:
            return self._frame(
                resolved=resolved,
                state_before=state_before,
                event_contract=event_contract,
                status="blocked",
                issues=tuple(preflight_issues),
            )

        fact_ids = {
            fact_id
            for requirement in event_contract.requirements
            for fact_id in requirement.runtime_binding.fact_ids
        }
        domains = {
            domain
            for requirement in event_contract.requirements
            for domain in requirement.runtime_binding.semantic_domains
        }
        lifecycle_ids = {
            requirement.runtime_binding.lifecycle_id
            for requirement in event_contract.requirements
            if requirement.runtime_binding.lifecycle_id
        }
        facts_by_id = {fact.fact_id: fact for fact in state_before.facts}
        relevant_facts = tuple(
            _fact_projection(facts_by_id[fact_id])
            for fact_id in sorted(fact_ids & set(facts_by_id))
        )
        relevant_subjects = {fact.subject for fact in relevant_facts}
        issues: list[RuntimeCompileIssue] = [
            _issue(
                "missing_bound_fact",
                artifact_ids=(fact_id,),
                reason="event runtime binding references a fact absent from state before",
            )
            for fact_id in sorted(fact_ids - set(facts_by_id))
        ]

        activated_rules: list[ActivatedRuntimeRule] = []
        excluded: list[ExcludedRuntimeArtifact] = []
        for rule in resolved.active_rules:
            is_relevant = rule.authority == "kernel" or _domain(rule.semantic_key) in domains
            if not is_relevant:
                continue
            if not _scope_applies(
                rule.scope,
                project_id=resolved.project_id,
                section=event_contract.section,
                subsection=event_contract.subsection,
                relevant_subjects=relevant_subjects,
                relevant_lifecycle_ids=lifecycle_ids,
            ):
                excluded.append(
                    _excluded(rule, artifact_type="rule", reason="scope_mismatch")
                )
                continue
            reason = (
                "universal_runtime_meta_rule"
                if rule.authority == "kernel"
                else f"event_semantic_domain:{_domain(rule.semantic_key)}"
            )
            activated_rules.append(_activated_rule(rule, reason))
        for rule in resolved.inactive_candidate_rules:
            if _domain(rule.semantic_key) in domains:
                excluded.append(
                    _excluded(
                        rule,
                        artifact_type="rule",
                        reason="inactive_candidate",
                    )
                )

        active_lifecycles = {
            lifecycle.lifecycle_id: lifecycle
            for lifecycle in resolved.active_lifecycles
        }
        activated_lifecycles: dict[str, ActivatedRuntimeLifecycle] = {}
        transition_options: list[RuntimeTransitionOption] = []
        event_boundaries: list[CompiledEventBoundary] = []
        for requirement in event_contract.requirements:
            binding = requirement.runtime_binding
            boundary_transition_ids: list[str] = []
            bridge_ids: list[str] = []
            boundary_status: EventBoundaryStatus = "ready"
            lifecycle = (
                active_lifecycles.get(binding.lifecycle_id)
                if binding.lifecycle_id
                else None
            )
            state_fact = (
                facts_by_id.get(binding.lifecycle_state_fact_id)
                if binding.lifecycle_state_fact_id
                else None
            )
            if binding.lifecycle_id and lifecycle is None:
                issues.append(
                    _issue(
                        "missing_bound_lifecycle",
                        artifact_ids=(binding.lifecycle_id,),
                        reason="event runtime binding references an inactive or missing lifecycle",
                    )
                )
                boundary_status = "unresolved"
            elif binding.lifecycle_id and state_fact is None:
                boundary_status = "unresolved"
            elif lifecycle is not None and state_fact is not None:
                activated_lifecycles[lifecycle.lifecycle_id] = (
                    ActivatedRuntimeLifecycle(
                        lifecycle_id=lifecycle.lifecycle_id,
                        semantic_key=lifecycle.semantic_key,
                        authority=lifecycle.authority,
                        enforcement=lifecycle.enforcement,
                        current_state_fact_id=state_fact.fact_id,
                        current_state=str(state_fact.value),
                        activation_reason=f"required_by_event:{requirement.event_id}",
                        source_id=lifecycle.provenance.source_id,
                        source_hash=lifecycle.provenance.source_hash,
                    )
                )
                transitions_by_id = {
                    transition.transition_id: transition
                    for transition in lifecycle.transitions
                }
                current_state = str(state_fact.value)
                preceding: list[str] = []
                for required_id in binding.required_transition_ids:
                    transition = transitions_by_id.get(required_id)
                    if transition is None:
                        issues.append(
                            _issue(
                                "missing_required_transition",
                                artifact_ids=(lifecycle.lifecycle_id, required_id),
                                reason="MUST_EVENT names a transition absent from lifecycle",
                            )
                        )
                        boundary_status = "unresolved"
                        continue
                    bridge = _shortest_path(
                        lifecycle, current_state, transition.from_state
                    )
                    if bridge is None:
                        issues.append(
                            _issue(
                                "unbridgeable_must_event",
                                artifact_ids=(lifecycle.lifecycle_id, required_id),
                                reason="no legal lifecycle path reaches required transition",
                            )
                        )
                        boundary_status = "unresolved"
                        continue
                    for bridge_transition in bridge:
                        transition_options.append(
                            RuntimeTransitionOption(
                                event_id=requirement.event_id,
                                lifecycle_id=lifecycle.lifecycle_id,
                                transition_id=bridge_transition.transition_id,
                                from_state=bridge_transition.from_state,
                                to_state=bridge_transition.to_state,
                                availability=(
                                    "currently_applicable"
                                    if not preceding
                                    else "requires_prior_transition"
                                ),
                                required_by_event=False,
                                preceding_transition_ids=tuple(preceding),
                                guards=bridge_transition.guards,
                            )
                        )
                        bridge_ids.append(bridge_transition.transition_id)
                        preceding.append(bridge_transition.transition_id)
                        current_state = bridge_transition.to_state
                    transition_options.append(
                        RuntimeTransitionOption(
                            event_id=requirement.event_id,
                            lifecycle_id=lifecycle.lifecycle_id,
                            transition_id=transition.transition_id,
                            from_state=transition.from_state,
                            to_state=transition.to_state,
                            availability=(
                                "currently_applicable"
                                if not preceding
                                else "requires_prior_transition"
                            ),
                            required_by_event=True,
                            preceding_transition_ids=tuple(preceding),
                            guards=transition.guards,
                        )
                    )
                    boundary_transition_ids.append(transition.transition_id)
                    preceding.append(transition.transition_id)
                    current_state = transition.to_state
                if bridge_ids and boundary_status != "unresolved":
                    boundary_status = "requires_bridge"

            event_boundaries.append(
                CompiledEventBoundary(
                    event_id=requirement.event_id,
                    description=requirement.description,
                    required=requirement.required,
                    relevant_fact_ids=tuple(sorted(binding.fact_ids)),
                    transition_ids=tuple(boundary_transition_ids),
                    bridge_transition_ids=tuple(bridge_ids),
                    status=boundary_status,
                )
            )

        for lifecycle in resolved.inactive_candidate_lifecycles:
            if _domain(lifecycle.semantic_key) in domains:
                excluded.append(
                    _excluded(
                        lifecycle,
                        artifact_type="lifecycle",
                        reason="inactive_candidate",
                    )
                )
        unknowns = tuple(
            RuntimeUnknown(
                fact_id=fact.fact_id,
                subject=fact.subject,
                predicate=fact.predicate,
                reason="canonical_state_epistemic_status_unknown",
            )
            for fact in relevant_facts
            if fact.epistemic_status == "unknown"
        )
        status: FrameStatus = "partial" if issues else "complete"
        ordered_facts = tuple(sorted(relevant_facts, key=lambda item: item.fact_id))
        ordered_rules = tuple(sorted(activated_rules, key=lambda item: item.rule_id))
        ordered_lifecycles = tuple(
            sorted(activated_lifecycles.values(), key=lambda item: item.lifecycle_id)
        )
        ordered_excluded = tuple(
            sorted(
                excluded,
                key=lambda item: (item.artifact_type, item.artifact_id),
            )
        )
        ordered_transitions = tuple(
            sorted(
                transition_options,
                key=lambda item: (
                    item.event_id,
                    item.preceding_transition_ids,
                    item.transition_id,
                ),
            )
        )
        ordered_boundaries = tuple(
            sorted(event_boundaries, key=lambda item: item.event_id)
        )
        relevant_source_hash = canonical_hash(
            {
                "facts": ordered_facts,
                "rules": ordered_rules,
                "lifecycles": ordered_lifecycles,
                "excluded": ordered_excluded,
                "events": ordered_boundaries,
            }
        )
        return self._frame(
            resolved=resolved,
            state_before=state_before,
            event_contract=event_contract,
            status=status,
            relevant_source_hash=relevant_source_hash,
            facts=ordered_facts,
            activated_rules=ordered_rules,
            activated_lifecycles=ordered_lifecycles,
            excluded_artifacts=ordered_excluded,
            transition_options=ordered_transitions,
            event_boundaries=ordered_boundaries,
            unknowns=tuple(sorted(unknowns, key=lambda item: item.fact_id)),
            issues=tuple(sorted(issues, key=lambda item: item.issue_id)),
        )

    def _frame(
        self,
        *,
        resolved: ResolvedWorldConstitution,
        state_before: CanonicalWorldState,
        event_contract: SubsectionEventContract,
        status: FrameStatus,
        relevant_source_hash: str | None = None,
        facts: tuple[RuntimeFactProjection, ...] = (),
        activated_rules: tuple[ActivatedRuntimeRule, ...] = (),
        activated_lifecycles: tuple[ActivatedRuntimeLifecycle, ...] = (),
        excluded_artifacts: tuple[ExcludedRuntimeArtifact, ...] = (),
        transition_options: tuple[RuntimeTransitionOption, ...] = (),
        event_boundaries: tuple[CompiledEventBoundary, ...] = (),
        unknowns: tuple[RuntimeUnknown, ...] = (),
        issues: tuple[RuntimeCompileIssue, ...] = (),
    ) -> SceneRuntimeFrame:
        source_hash = relevant_source_hash or canonical_hash(
            {"status": status, "issues": issues}
        )
        frame_identity = {
            "project_id": event_contract.project_id,
            "section": event_contract.section,
            "subsection": event_contract.subsection,
            "state_revision": state_before.revision,
            "event_contract_id": event_contract.contract_id,
            "relevant_source_hash": source_hash,
        }
        return SceneRuntimeFrame(
            frame_id=f"runtime-frame:{canonical_hash(frame_identity)[:24]}",
            project_id=event_contract.project_id,
            section=event_contract.section,
            subsection=event_contract.subsection,
            state_revision=state_before.revision,
            constitution_version=resolved.constitution_version,
            kernel_version=resolved.kernel_version,
            event_contract_id=event_contract.contract_id,
            event_contract_hash=event_contract.artifact_hash,
            relevant_source_hash=source_hash,
            status=status,
            facts=facts,
            activated_rules=activated_rules,
            activated_lifecycles=activated_lifecycles,
            excluded_artifacts=excluded_artifacts,
            transition_options=transition_options,
            event_boundaries=event_boundaries,
            unknowns=unknowns,
            issues=issues,
        )
