from pathlib import Path

import pytest

from app.writing.world_runtime_consumption import build_wr0c_consumer_registry
from app.writing.world_runtime_contracts import (
    ProjectWorldConstitution,
    RuleScope,
    StatePredicate,
)
from app.writing.world_runtime_kernel import (
    REQUIRED_KERNEL_RULE_IDS,
    KernelValidationReport,
    UniversalRuntimeKernel,
    build_minimal_universal_kernel,
    validate_minimal_universal_kernel,
)
from app.writing.world_runtime_resolver import WorldRuntimeResolver


ROOT = Path(__file__).resolve().parents[2]


def test_minimal_kernel_contains_only_five_valid_global_meta_rules():
    kernel = build_minimal_universal_kernel()
    report = validate_minimal_universal_kernel(kernel)
    assert report.valid
    assert report.issues == ()
    assert tuple(rule.rule_id for rule in kernel.rules) == REQUIRED_KERNEL_RULE_IDS
    assert len(kernel.rules) == 5
    assert all(rule.authority == "kernel" for rule in kernel.rules)
    assert all(rule.enforcement == "block" for rule in kernel.rules)
    assert all(rule.scope == RuleScope() for rule in kernel.rules)
    assert all(not rule.effects for rule in kernel.rules)
    assert all(rule.prerequisites[0].subject.startswith("$") for rule in kernel.rules)


def test_kernel_hash_is_independent_of_rule_order():
    kernel = build_minimal_universal_kernel()
    reversed_kernel = UniversalRuntimeKernel(rules=tuple(reversed(kernel.rules)))
    assert kernel.artifact_hash == reversed_kernel.artifact_hash
    assert validate_minimal_universal_kernel(reversed_kernel).valid


@pytest.mark.parametrize("removed_rule_id", REQUIRED_KERNEL_RULE_IDS)
def test_each_required_kernel_rule_is_individually_required(removed_rule_id):
    kernel = build_minimal_universal_kernel()
    mutated = UniversalRuntimeKernel(
        rules=tuple(rule for rule in kernel.rules if rule.rule_id != removed_rule_id)
    )
    report = validate_minimal_universal_kernel(mutated)
    assert not report.valid
    assert any(
        item.code == "missing_required_rule"
        and item.rule_ids == (removed_rule_id,)
        for item in report.issues
    )


@pytest.mark.parametrize("mutated_rule_id", REQUIRED_KERNEL_RULE_IDS)
def test_each_kernel_rule_rejects_predicate_mutation(mutated_rule_id):
    kernel = build_minimal_universal_kernel()
    rules = []
    for rule in kernel.rules:
        if rule.rule_id != mutated_rule_id:
            rules.append(rule)
            continue
        original = rule.prerequisites[0]
        rules.append(
            rule.model_copy(
                update={
                    "prerequisites": (
                        StatePredicate(
                            subject=original.subject,
                            predicate=f"mutated:{original.predicate}",
                            operator=original.operator,
                            expected=original.expected,
                        ),
                    )
                }
            )
        )
    report = validate_minimal_universal_kernel(
        UniversalRuntimeKernel(rules=tuple(rules))
    )
    assert not report.valid
    assert any(
        item.code == "kernel_rule_signature_mismatch"
        and item.rule_ids == (mutated_rule_id,)
        for item in report.issues
    )


def test_kernel_rejects_genre_or_project_rule_and_non_global_scope():
    kernel = build_minimal_universal_kernel()
    base = kernel.rules[0]
    domain_rule = base.model_copy(
        update={
            "rule_id": "kernel.modern_storefront_business_hours",
            "semantic_key": "storefront.business_hours",
        }
    )
    scoped_rule = base.model_copy(
        update={"scope": RuleScope(project_id="project-1", entity_ids=("bakery",))}
    )
    report = validate_minimal_universal_kernel(
        UniversalRuntimeKernel(rules=(*kernel.rules, domain_rule, scoped_rule))
    )
    codes = {item.code for item in report.issues}
    assert "unexpected_rule" in codes
    assert "duplicate_rule_id" in codes
    assert "non_global_kernel_scope" in codes


def test_kernel_rejects_authority_or_enforcement_downgrade():
    kernel = build_minimal_universal_kernel()
    target = kernel.rules[0]
    downgraded = target.model_copy(
        update={"authority": "project_explicit", "enforcement": "suggest"}
    )
    mutated = UniversalRuntimeKernel(
        rules=tuple(downgraded if rule is target else rule for rule in kernel.rules)
    )
    report = validate_minimal_universal_kernel(mutated)
    assert not report.valid
    assert any(
        item.code == "kernel_rule_signature_mismatch"
        and target.rule_id in item.rule_ids
        for item in report.issues
    )


def test_resolver_consumes_only_a_validated_kernel():
    kernel = build_minimal_universal_kernel()
    constitution = ProjectWorldConstitution(project_id="project-1", version="1")
    resolved = WorldRuntimeResolver().resolve(
        constitution=constitution,
        kernel=kernel,
    )
    assert {item.rule_id for item in resolved.active_rules} == set(
        REQUIRED_KERNEL_RULE_IDS
    )
    assert resolved.kernel_version == kernel.version
    assert resolved.kernel_hash == kernel.artifact_hash
    assert not resolved.conflict_report.has_blocking

    target = kernel.rules[0]
    invalid_kernel = UniversalRuntimeKernel(
        rules=tuple(
            rule.model_copy(update={"enforcement": "suggest"})
            if rule is target
            else rule
            for rule in kernel.rules
        )
    )
    rejected = WorldRuntimeResolver().resolve(
        constitution=constitution,
        kernel=invalid_kernel,
    )
    assert rejected.active_rules == ()
    assert {item.code for item in rejected.conflict_report.conflicts} == {
        "invalid_universal_kernel"
    }


def test_wr0c_artifacts_have_complete_consumers_and_unique_authority():
    registry = build_wr0c_consumer_registry()
    contracts = {item.artifact_type: item for item in registry.contracts}
    assert set(contracts["UniversalRuntimeKernel"].stable_fields) == set(
        UniversalRuntimeKernel.model_fields
    )
    assert set(contracts["KernelValidationReport"].stable_fields) == set(
        KernelValidationReport.model_fields
    )
    assert registry.orphaned_stable_fields == ()


def test_wr0c_kernel_is_not_imported_by_production_facades():
    writing_init = (ROOT / "app" / "writing" / "__init__.py").read_text("utf-8")
    writer = (ROOT / "app" / "agents" / "writer.py").read_text("utf-8")
    assert "world_runtime_kernel" not in writing_init
    assert "world_runtime_kernel" not in writer
