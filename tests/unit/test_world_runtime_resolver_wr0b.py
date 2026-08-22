from pathlib import Path

from pydantic import ValidationError
import pytest

from app.writing.world_runtime_contracts import (
    CandidatePack,
    Lifecycle,
    LifecycleTransition,
    ProjectWorldConstitution,
    ProvenanceRef,
    RuleScope,
    StateEffect,
    WorldRule,
)
from app.writing.world_runtime_resolver import (
    ConflictReport,
    ResolvedWorldConstitution,
    UserOverrideSet,
    WorldRuntimeResolver,
)
from app.writing.world_runtime_consumption import build_wr0b_consumer_registry


ROOT = Path(__file__).resolve().parents[2]


def _source(source_id: str) -> ProvenanceRef:
    return ProvenanceRef(
        source_id=source_id,
        source_type="fixture",
        source_hash=f"hash:{source_id}",
        producer="wr0b_fixture",
    )


def _rule(
    rule_id: str,
    *,
    semantic_key: str = "storefront.opening",
    authority: str = "project_explicit",
    enforcement: str = "block",
    scope: RuleScope | None = None,
    value: str = "scheduled_day_required",
    overrides_rule_ids: tuple[str, ...] = (),
) -> WorldRule:
    return WorldRule(
        rule_id=rule_id,
        semantic_key=semantic_key,
        kind="precondition",
        authority=authority,
        enforcement=enforcement,
        activation_enforcement=(
            "suggest" if authority == "pack_candidate" else None
        ),
        scope=scope or RuleScope(project_id="project-1"),
        effects=(
            StateEffect(
                subject="storefront",
                predicate=semantic_key,
                operation="set",
                value=value,
            ),
        ),
        overrides_rule_ids=overrides_rule_ids,
        provenance=_source(rule_id),
        version="1",
    )


def _lifecycle(
    lifecycle_id: str,
    *,
    authority: str = "project_explicit",
    enforcement: str = "block",
    terminal_state: str = "published",
) -> Lifecycle:
    return Lifecycle(
        lifecycle_id=lifecycle_id,
        semantic_key="article.publication.lifecycle",
        states=("draft", "published"),
        initial_state="draft",
        transitions=(
            LifecycleTransition(
                transition_id=f"{lifecycle_id}:publish",
                from_state="draft",
                to_state="published",
            ),
        ),
        terminal_states=(terminal_state,),
        authority=authority,
        enforcement=enforcement,
        activation_enforcement=(
            "suggest" if authority == "pack_candidate" else None
        ),
        scope=RuleScope(project_id="project-1"),
        provenance=_source(lifecycle_id),
        version="1",
    )


def _candidate_pack(
    version: str,
    *rules: WorldRule,
    lifecycles: tuple[Lifecycle, ...] = (),
) -> CandidatePack:
    return CandidatePack(
        pack_id="modern-urban",
        version=version,
        rules=rules,
        lifecycles=lifecycles,
    )


def test_resolution_is_order_independent_for_rules_overrides_and_packs():
    rule_a = _rule("rule:a", semantic_key="a")
    rule_b = _rule("rule:b", semantic_key="b")
    override_a = _rule(
        "override:a",
        semantic_key="a",
        authority="user_override",
        value="user-value",
    )
    candidate_a = _rule(
        "candidate:a",
        semantic_key="candidate.a",
        authority="pack_candidate",
        enforcement="inactive",
    )
    candidate_b = _rule(
        "candidate:b",
        semantic_key="candidate.b",
        authority="pack_candidate",
        enforcement="inactive",
    )
    pack = _candidate_pack("1", candidate_a, candidate_b)
    reversed_pack = _candidate_pack("1", candidate_b, candidate_a)

    first = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1",
            version="1",
            rules=(rule_a, rule_b),
            bound_candidate_packs=("modern-urban@1",),
        ),
        candidate_packs=(pack,),
        user_overrides=UserOverrideSet(
            project_id="project-1", version="1", rules=(override_a,)
        ),
    )
    second = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1",
            version="1",
            rules=(rule_b, rule_a),
            bound_candidate_packs=("modern-urban@1",),
        ),
        candidate_packs=(reversed_pack,),
        user_overrides=UserOverrideSet(
            project_id="project-1", version="1", rules=(override_a,)
        ),
    )
    assert first.resolved_hash == second.resolved_hash
    assert first.conflict_report.report_hash == second.conflict_report.report_hash


def test_bound_pack_candidates_remain_inactive():
    candidate = _rule(
        "candidate:opening",
        authority="pack_candidate",
        enforcement="inactive",
    )
    result = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1",
            version="1",
            bound_candidate_packs=("modern-urban@1",),
        ),
        candidate_packs=(_candidate_pack("1", candidate),),
    )
    assert result.active_rules == ()
    assert result.inactive_candidate_rules == (candidate,)
    assert not result.conflict_report.has_blocking


def test_pack_upgrade_cannot_silently_replace_bound_version():
    v2_candidate = _rule(
        "candidate:v2",
        authority="pack_candidate",
        enforcement="inactive",
    )
    result = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1",
            version="1",
            bound_candidate_packs=("modern-urban@1",),
        ),
        candidate_packs=(_candidate_pack("2", v2_candidate),),
    )
    assert result.inactive_candidate_rules == ()
    assert {item.code for item in result.conflict_report.conflicts} == {
        "missing_bound_pack_version"
    }
    assert any(
        item.artifact_id == "modern-urban@2"
        and item.action == "ignored_unbound_pack"
        for item in result.decisions
    )


def test_same_pack_version_is_order_insensitive_but_content_conflict_is_blocking():
    candidate_a = _rule(
        "candidate:a",
        semantic_key="candidate.a",
        authority="pack_candidate",
        enforcement="inactive",
    )
    candidate_b = _rule(
        "candidate:b",
        semantic_key="candidate.b",
        authority="pack_candidate",
        enforcement="inactive",
    )
    first = _candidate_pack("1", candidate_a, candidate_b)
    reordered = _candidate_pack("1", candidate_b, candidate_a)
    assert first.artifact_hash == reordered.artifact_hash

    clean = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1",
            version="1",
            bound_candidate_packs=("modern-urban@1",),
        ),
        candidate_packs=(first, reordered),
    )
    assert not clean.conflict_report.has_blocking

    changed = _candidate_pack(
        "1",
        candidate_a.model_copy(
            update={
                "effects": (
                    StateEffect(
                        subject="storefront",
                        predicate="candidate.a",
                        operation="set",
                        value="changed",
                    ),
                )
            }
        ),
        candidate_b,
    )
    conflicted = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1",
            version="1",
            bound_candidate_packs=("modern-urban@1",),
        ),
        candidate_packs=(first, changed),
    )
    assert {item.code for item in conflicted.conflict_report.conflicts} == {
        "duplicate_pack_version"
    }


def test_same_layer_same_scope_conflict_is_reported_not_guessed():
    left = _rule("rule:left", value="open")
    right = _rule("rule:right", value="closed")
    result = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1", version="1", rules=(left, right)
        )
    )
    assert result.active_rules == ()
    assert result.conflict_report.has_blocking
    assert result.conflict_report.conflicts[0].code == "same_layer_rule_conflict"


def test_user_override_wins_same_scope_by_authority():
    project_rule = _rule("rule:project", value="project")
    user_rule = _rule(
        "rule:user",
        authority="user_override",
        value="user",
    )
    result = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1", version="1", rules=(project_rule,)
        ),
        user_overrides=UserOverrideSet(
            project_id="project-1", version="1", rules=(user_rule,)
        ),
    )
    assert result.active_rules == (user_rule,)
    assert any(
        item.artifact_id == project_rule.rule_id and item.action == "shadowed"
        for item in result.decisions
    )


def test_scoped_exception_keeps_general_rule_and_only_overrides_declared_target():
    general = _rule("rule:no-teleport", semantic_key="movement.teleport")
    exception = _rule(
        "rule:character-a-can-teleport",
        semantic_key="movement.teleport",
        authority="user_override",
        scope=RuleScope(project_id="project-1", entity_ids=("character-a",)),
        value="allowed_by_space_ability",
        overrides_rule_ids=(general.rule_id,),
    )
    unrelated = _rule(
        "rule:knowledge-path",
        semantic_key="knowledge.transmission",
        value="path_required",
    )
    result = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1", version="1", rules=(general, unrelated)
        ),
        user_overrides=UserOverrideSet(
            project_id="project-1", version="1", rules=(exception,)
        ),
    )
    assert {item.rule_id for item in result.active_rules} == {
        general.rule_id,
        exception.rule_id,
        unrelated.rule_id,
    }
    assert any(
        item.artifact_id == exception.rule_id and item.action == "valid_exception"
        for item in result.decisions
    )
    assert any(
        item.artifact_id == exception.rule_id and item.action == "scope_precedence"
        for item in result.decisions
    )
    assert not result.conflict_report.has_blocking


def test_exception_cannot_expand_target_scope():
    scoped_target = _rule(
        "rule:character-a-limit",
        scope=RuleScope(project_id="project-1", entity_ids=("character-a",)),
    )
    broad_exception = _rule(
        "rule:global-exception",
        authority="user_override",
        scope=RuleScope(project_id="project-1"),
        overrides_rule_ids=(scoped_target.rule_id,),
        value="global-allow",
    )
    result = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1", version="1", rules=(scoped_target,)
        ),
        user_overrides=UserOverrideSet(
            project_id="project-1", version="1", rules=(broad_exception,)
        ),
    )
    assert {item.rule_id for item in result.active_rules} == {scoped_target.rule_id}
    assert "override_scope_expansion" in {
        item.code for item in result.conflict_report.conflicts
    }
    assert any(
        item.artifact_id == broad_exception.rule_id
        and item.action == "rejected_conflict"
        for item in result.decisions
    )
    assert not any(
        item.artifact_id == broad_exception.rule_id and item.action == "selected"
        for item in result.decisions
    )


def test_lifecycle_conflict_and_override_follow_same_authority_rules():
    project = _lifecycle("lifecycle:project")
    user = _lifecycle("lifecycle:user", authority="user_override")
    result = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1", version="1", lifecycles=(project,)
        ),
        user_overrides=UserOverrideSet(
            project_id="project-1", version="1", lifecycles=(user,)
        ),
    )
    assert result.active_lifecycles == (user,)
    assert not result.conflict_report.has_blocking


def test_same_layer_lifecycle_conflict_is_reported():
    left = _lifecycle("lifecycle:left")
    right = Lifecycle(
        lifecycle_id="lifecycle:right",
        semantic_key="article.publication.lifecycle",
        states=("draft", "submitted", "published"),
        initial_state="draft",
        transitions=(
            LifecycleTransition(
                transition_id="submit",
                from_state="draft",
                to_state="submitted",
            ),
        ),
        terminal_states=("published",),
        authority="project_explicit",
        enforcement="block",
        scope=RuleScope(project_id="project-1"),
        provenance=_source("lifecycle:right"),
        version="1",
    )
    result = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1", version="1", lifecycles=(left, right)
        )
    )
    assert result.active_lifecycles == ()
    assert {item.code for item in result.conflict_report.conflicts} == {
        "same_layer_lifecycle_conflict"
    }


def test_override_project_mismatch_is_blocking_and_not_applied():
    project_rule = _rule("rule:project")
    foreign_override = _rule(
        "rule:foreign", authority="user_override", value="foreign"
    )
    result = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1", version="1", rules=(project_rule,)
        ),
        user_overrides=UserOverrideSet(
            project_id="project-2", version="1", rules=(foreign_override,)
        ),
    )
    assert result.active_rules == (project_rule,)
    assert {item.code for item in result.conflict_report.conflicts} == {
        "project_mismatch"
    }


def test_user_override_set_rejects_non_user_authority():
    with pytest.raises(ValidationError, match="user_override authority"):
        UserOverrideSet(
            project_id="project-1",
            version="1",
            rules=(_rule("rule:not-user"),),
        )


def test_wr0b_resolver_artifacts_have_consumers_and_no_double_authority():
    registry = build_wr0b_consumer_registry()
    artifact_models = {
        "UserOverrideSet": UserOverrideSet,
        "ResolvedWorldConstitution": ResolvedWorldConstitution,
        "ConflictReport": ConflictReport,
    }
    contracts = {item.artifact_type: item for item in registry.contracts}
    for artifact_type, model in artifact_models.items():
        assert set(contracts[artifact_type].stable_fields) == set(model.model_fields)
    assert registry.orphaned_stable_fields == ()


def test_wr0b_resolver_is_not_imported_by_production_facades():
    writing_init = (ROOT / "app" / "writing" / "__init__.py").read_text("utf-8")
    writer = (ROOT / "app" / "agents" / "writer.py").read_text("utf-8")
    assert "world_runtime_resolver" not in writing_init
    assert "world_runtime_resolver" not in writer
