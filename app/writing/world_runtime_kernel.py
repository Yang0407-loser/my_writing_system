"""WR0-C minimal universal meta-rules for the world runtime.

The kernel defines invariants about state transitions and commits.  It does
not define genre facts (for example, whether teleportation exists), evaluate a
story, or participate in the production Writer pipeline.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .world_runtime_contracts import (
    FrozenRuntimeModel,
    ProvenanceRef,
    RuleScope,
    StatePredicate,
    WorldRule,
    canonical_hash,
)


WORLD_RUNTIME_KERNEL_VERSION = "universal-runtime-kernel-v1"

KernelIssueCode = Literal[
    "missing_required_rule",
    "unexpected_rule",
    "duplicate_rule_id",
    "kernel_rule_signature_mismatch",
    "non_global_kernel_scope",
]


_REQUIRED_RULE_SIGNATURES = {
    "kernel.state_change_requires_mechanism": {
        "semantic_key": "meta.state_change.causality",
        "kind": "invariant",
        "subject": "$state_change",
        "predicate": "has_source_or_declared_mechanism",
    },
    "kernel.transition_requires_prerequisites": {
        "semantic_key": "meta.transition.prerequisites",
        "kind": "precondition",
        "subject": "$transition",
        "predicate": "all_declared_prerequisites_satisfied",
    },
    "kernel.knowledge_requires_path": {
        "semantic_key": "meta.knowledge.acquisition_path",
        "kind": "precondition",
        "subject": "$knowledge_acquisition",
        "predicate": "has_transmission_or_perception_path",
    },
    "kernel.commit_requires_current_revision": {
        "semantic_key": "meta.commit.revision",
        "kind": "precondition",
        "subject": "$commit",
        "predicate": "base_revision_matches_current_revision",
    },
    "kernel.delta_is_idempotent": {
        "semantic_key": "meta.delta.idempotency",
        "kind": "invariant",
        "subject": "$delta_commit",
        "predicate": "same_idempotency_key_has_single_effect",
    },
}

REQUIRED_KERNEL_RULE_IDS = tuple(sorted(_REQUIRED_RULE_SIGNATURES))


class UniversalRuntimeKernel(FrozenRuntimeModel):
    kernel_id: str = "universal-runtime-kernel"
    version: str = WORLD_RUNTIME_KERNEL_VERSION
    rules: tuple[WorldRule, ...] = ()
    schema_version: str = WORLD_RUNTIME_KERNEL_VERSION

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(
            {
                "kernel_id": self.kernel_id,
                "version": self.version,
                "rules": sorted(
                    self.rules,
                    key=lambda item: (item.rule_id, canonical_hash(item)),
                ),
                "schema_version": self.schema_version,
            }
        )


class KernelValidationIssue(FrozenRuntimeModel):
    issue_id: str = Field(min_length=1)
    code: KernelIssueCode
    rule_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_KERNEL_VERSION


class KernelValidationReport(FrozenRuntimeModel):
    kernel_id: str = Field(min_length=1)
    kernel_hash: str = Field(min_length=1)
    valid: bool
    issues: tuple[KernelValidationIssue, ...] = ()
    schema_version: str = WORLD_RUNTIME_KERNEL_VERSION

    @property
    def report_hash(self) -> str:
        return canonical_hash(self)


def _kernel_provenance(rule_id: str) -> ProvenanceRef:
    signature = _REQUIRED_RULE_SIGNATURES[rule_id]
    return ProvenanceRef(
        source_id=f"{WORLD_RUNTIME_KERNEL_VERSION}:{rule_id}",
        source_type="universal_runtime_kernel",
        source_hash=canonical_hash(signature),
        producer="build_minimal_universal_kernel",
    )


def _build_rule(rule_id: str) -> WorldRule:
    signature = _REQUIRED_RULE_SIGNATURES[rule_id]
    return WorldRule(
        rule_id=rule_id,
        semantic_key=signature["semantic_key"],
        kind=signature["kind"],
        authority="kernel",
        enforcement="block",
        scope=RuleScope(),
        prerequisites=(
            StatePredicate(
                subject=signature["subject"],
                predicate=signature["predicate"],
                operator="equals",
                expected=True,
            ),
        ),
        provenance=_kernel_provenance(rule_id),
        version=WORLD_RUNTIME_KERNEL_VERSION,
    )


def build_minimal_universal_kernel() -> UniversalRuntimeKernel:
    """Build the exact five-rule WR0-C kernel in canonical order."""

    return UniversalRuntimeKernel(
        rules=tuple(_build_rule(rule_id) for rule_id in REQUIRED_KERNEL_RULE_IDS)
    )


def _global_scope(scope: RuleScope) -> bool:
    return scope == RuleScope()


def _signature_matches(rule: WorldRule, expected: dict[str, str]) -> bool:
    if (
        rule.semantic_key != expected["semantic_key"]
        or rule.kind != expected["kind"]
        or rule.authority != "kernel"
        or rule.enforcement != "block"
        or rule.activation_enforcement is not None
        or rule.conditions
        or rule.effects
        or rule.overrides_rule_ids
        or len(rule.prerequisites) != 1
    ):
        return False
    prerequisite = rule.prerequisites[0]
    return (
        prerequisite.subject == expected["subject"]
        and prerequisite.predicate == expected["predicate"]
        and prerequisite.operator == "equals"
        and prerequisite.expected is True
        and prerequisite.fact_id is None
    )


def _issue(
    code: KernelIssueCode, *, rule_ids: tuple[str, ...], reason: str
) -> KernelValidationIssue:
    body = {"code": code, "rule_ids": tuple(sorted(rule_ids)), "reason": reason}
    return KernelValidationIssue(
        issue_id=f"kernel-issue:{canonical_hash(body)[:24]}",
        code=code,
        rule_ids=tuple(sorted(rule_ids)),
        reason=reason,
    )


def validate_minimal_universal_kernel(
    kernel: UniversalRuntimeKernel,
) -> KernelValidationReport:
    """Validate kernel integrity; this is not story-transition validation."""

    issues: list[KernelValidationIssue] = []
    by_id: dict[str, list[WorldRule]] = {}
    for rule in kernel.rules:
        by_id.setdefault(rule.rule_id, []).append(rule)

    required = set(REQUIRED_KERNEL_RULE_IDS)
    present = set(by_id)
    for missing in sorted(required - present):
        issues.append(
            _issue(
                "missing_required_rule",
                rule_ids=(missing,),
                reason="required universal meta-rule is absent",
            )
        )
    for unexpected in sorted(present - required):
        issues.append(
            _issue(
                "unexpected_rule",
                rule_ids=(unexpected,),
                reason="minimal kernel cannot contain genre or project rules",
            )
        )
    for rule_id, rules in sorted(by_id.items()):
        if len(rules) > 1:
            issues.append(
                _issue(
                    "duplicate_rule_id",
                    rule_ids=(rule_id,),
                    reason="kernel rule IDs must be unique",
                )
            )
        if rule_id not in required:
            continue
        for rule in rules:
            if not _global_scope(rule.scope):
                issues.append(
                    _issue(
                        "non_global_kernel_scope",
                        rule_ids=(rule_id,),
                        reason="universal meta-rules cannot target project entities",
                    )
                )
            if not _signature_matches(rule, _REQUIRED_RULE_SIGNATURES[rule_id]):
                issues.append(
                    _issue(
                        "kernel_rule_signature_mismatch",
                        rule_ids=(rule_id,),
                        reason="kernel rule differs from its frozen meta-rule signature",
                    )
                )

    ordered = tuple(
        sorted(
            {item.issue_id: item for item in issues}.values(),
            key=lambda item: item.issue_id,
        )
    )
    return KernelValidationReport(
        kernel_id=kernel.kernel_id,
        kernel_hash=kernel.artifact_hash,
        valid=not ordered,
        issues=ordered,
    )
