import json
from pathlib import Path

import pytest
import yaml

from app.writing.world_runtime_bakery_gold import (
    BAKERY_PROJECT_ID,
    build_saturday_bakery_gold_fixture,
)
from app.writing.world_runtime_compiler import WorldRuntimeCompiler
from app.writing.world_runtime_confirmation import (
    ConfirmationAction,
    apply_confirmation_actions,
    build_world_runtime_debug_view,
    replay_constitution_change,
)
from app.writing.world_runtime_consumption import build_wr0g_consumer_registry
from app.writing.world_runtime_contracts import (
    ProjectWorldConstitution,
    ProvenanceRef,
    RuleScope,
    StateEffect,
    StatePredicate,
    WorldRule,
    canonical_hash,
)
from app.writing.world_runtime_event_contracts import SubsectionEventContract
from app.writing.world_runtime_kernel import build_minimal_universal_kernel
from app.writing.world_runtime_pack_modern_urban import (
    MODERN_URBAN_CN_2020S_PACK_REF,
    build_modern_urban_cn_2020s_candidate_pack,
)
from app.writing.world_runtime_resolver import WorldRuntimeResolver


ROOT = Path(__file__).resolve().parents[2]


def _unconfirmed_constitution() -> ProjectWorldConstitution:
    return ProjectWorldConstitution(
        project_id=BAKERY_PROJECT_ID,
        version="unconfirmed-1",
        bound_candidate_packs=(MODERN_URBAN_CN_2020S_PACK_REF,),
    )


def _resolved(constitution):
    pack = build_modern_urban_cn_2020s_candidate_pack()
    return WorldRuntimeResolver().resolve(
        constitution=constitution,
        candidate_packs=(pack,),
        kernel=build_minimal_universal_kernel(),
    )


def _frame(constitution=None, contract=None):
    fixture = build_saturday_bakery_gold_fixture()
    constitution = constitution or _unconfirmed_constitution()
    contract = contract or fixture.event_contract
    resolved = _resolved(constitution)
    frame = WorldRuntimeCompiler().compile(
        resolved=resolved,
        state_before=fixture.state_before,
        event_contract=contract,
    )
    return fixture, resolved, frame


def _publication_contract(fixture):
    requirement = next(
        item
        for item in fixture.event_contract.requirements
        if item.event_id == "event:publish-article"
    )
    return SubsectionEventContract(
        contract_id="event-contract:publication-only",
        project_id=fixture.event_contract.project_id,
        section=2,
        subsection=1,
        requirements=(requirement,),
        provenance=fixture.event_contract.provenance,
    )


def _candidate_action(
    action_id,
    artifact_id,
    *,
    decision="confirm",
    artifact_type="rule",
    enforcement=None,
):
    return ConfirmationAction(
        action_id=action_id,
        decision=decision,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        candidate_pack_ref=MODERN_URBAN_CN_2020S_PACK_REF,
        scope=RuleScope(project_id=BAKERY_PROJECT_ID),
        enforcement=enforcement,
        rationale=f"fixture decision for {artifact_id}",
        user_input_hash=canonical_hash(
            {"action_id": action_id, "artifact_id": artifact_id, "decision": decision}
        ),
    )


def test_confirmation_queue_is_on_demand_not_a_full_world_questionnaire():
    fixture = build_saturday_bakery_gold_fixture()
    contract = _publication_contract(fixture)
    _, resolved, frame = _frame(contract=contract)
    pack = build_modern_urban_cn_2020s_candidate_pack()

    view = build_world_runtime_debug_view(
        resolved=resolved,
        frame=frame,
        candidate_packs=(pack,),
    )
    queued = {item.artifact_id for item in view.pending_confirmations}

    assert queued == {
        "modern-urban.publication.public-reaction-requires-reach",
        "modern-urban.publication.public-visibility-requires-publication",
        "modern-urban.lifecycle.publication",
    }
    assert len(queued) < len(pack.rules) + len(pack.lifecycles)
    assert all(item.priority == "high_impact" for item in view.pending_confirmations)


def test_full_bakery_event_surface_has_high_impact_and_provisional_tiers():
    _, resolved, frame = _frame()
    view = build_world_runtime_debug_view(
        resolved=resolved,
        frame=frame,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
    )
    counts = {
        priority: sum(
            item.priority == priority for item in view.pending_confirmations
        )
        for priority in ("blocking", "high_impact", "provisional")
    }

    assert counts == {"blocking": 0, "high_impact": 9, "provisional": 2}
    assert all(
        item.allowed_actions == ("confirm", "reject")
        for item in view.pending_confirmations
    )


def test_already_confirmed_semantics_are_not_asked_again():
    fixture = build_saturday_bakery_gold_fixture()
    resolved = _resolved(fixture.constitution)
    frame = WorldRuntimeCompiler().compile(
        resolved=resolved,
        state_before=fixture.state_before,
        event_contract=fixture.event_contract,
    )
    view = build_world_runtime_debug_view(
        resolved=resolved,
        frame=frame,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
    )

    assert view.pending_confirmations == ()


def test_confirming_one_candidate_materializes_only_that_user_override():
    constitution = _unconfirmed_constitution()
    action = _candidate_action(
        "action:confirm-publication-visibility",
        "modern-urban.publication.public-visibility-requires-publication",
    )

    result = apply_confirmation_actions(
        constitution=constitution,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
        actions=(action,),
    )

    assert len(result.resulting_constitution.rules) == 1
    confirmed = result.resulting_constitution.rules[0]
    assert confirmed.authority == "user_override"
    assert confirmed.enforcement == "block"
    assert confirmed.scope == RuleScope(project_id=BAKERY_PROJECT_ID)
    assert confirmed.provenance.source_type == "user_confirm"
    assert result.resulting_constitution.lifecycles == ()
    record = result.decision_ledger.records[0]
    assert record.candidate_pack_ref == MODERN_URBAN_CN_2020S_PACK_REF
    assert record.candidate_hash
    assert record.materialized_artifact_id == confirmed.rule_id
    resolved = _resolved(result.resulting_constitution)
    assert {
        rule.rule_id for rule in resolved.active_rules if rule.authority != "kernel"
    } == {confirmed.rule_id}
    assert len(resolved.inactive_candidate_rules) == 7
    assert len(resolved.inactive_candidate_lifecycles) == 4


def test_rejection_records_decision_without_creating_world_rule():
    constitution = _unconfirmed_constitution()
    action = _candidate_action(
        "action:reject-public-reaction",
        "modern-urban.publication.public-reaction-requires-reach",
        decision="reject",
    )
    result = apply_confirmation_actions(
        constitution=constitution,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
        actions=(action,),
    )
    fixture, resolved, frame = _frame(
        constitution=result.resulting_constitution,
        contract=_publication_contract(build_saturday_bakery_gold_fixture()),
    )
    view = build_world_runtime_debug_view(
        resolved=resolved,
        frame=frame,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
        decision_ledger=result.decision_ledger,
    )

    assert result.change_set.added_rules == ()
    assert result.resulting_constitution.rules == ()
    assert result.resulting_constitution.version != constitution.version
    assert action.artifact_id not in {
        item.artifact_id for item in view.pending_confirmations
    }
    assert fixture.state_before.revision == 7


def test_confirmed_lifecycle_gets_project_identity_and_remapped_transitions():
    action = _candidate_action(
        "action:confirm-publication-lifecycle",
        "modern-urban.lifecycle.publication",
        artifact_type="lifecycle",
    )
    result = apply_confirmation_actions(
        constitution=_unconfirmed_constitution(),
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
        actions=(action,),
    )
    lifecycle = result.resulting_constitution.lifecycles[0]

    assert lifecycle.authority == "user_override"
    assert lifecycle.lifecycle_id.startswith(f"confirmed:{BAKERY_PROJECT_ID}:")
    assert all(
        transition.transition_id.startswith(f"{lifecycle.lifecycle_id}.")
        for transition in lifecycle.transitions
    )
    assert all(
        effect.subject == f"${lifecycle.lifecycle_id}"
        for transition in lifecycle.transitions
        for effect in transition.effects
    )


def test_scoped_exception_is_narrow_versioned_and_resolver_valid():
    fixture = build_saturday_bakery_gold_fixture()
    target = next(
        rule
        for rule in fixture.constitution.rules
        if rule.rule_id == "bakery.explicit.storefront-schedule"
    )
    action = ConfirmationAction(
        action_id="action:temporary-opening-exception",
        decision="scoped_exception",
        artifact_type="rule",
        artifact_id="temporary-opening",
        scope=RuleScope(
            project_id=BAKERY_PROJECT_ID,
            entity_ids=("bakery:wild-bread",),
            location_ids=("bakery:wild-bread:storefront",),
            section=2,
            subsection=1,
        ),
        enforcement="block",
        target_rule_id=target.rule_id,
        conditions=(
            StatePredicate(
                subject="bakery:wild-bread",
                predicate="temporary_opening_declared",
                operator="equals",
                expected=True,
            ),
        ),
        effects=(
            StateEffect(
                subject="bakery:wild-bread:storefront",
                predicate="public_opening_allowed",
                operation="set",
                value=True,
            ),
        ),
        rationale="仅允许本小节明确声明的临时营业",
        user_input_hash=canonical_hash("temporary opening section 2.1"),
    )
    result = apply_confirmation_actions(
        constitution=fixture.constitution,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
        actions=(action,),
    )
    exception = result.change_set.added_rules[0]
    replayed = replay_constitution_change(
        constitution=fixture.constitution,
        change_set=result.change_set,
    )
    resolved = _resolved(replayed)

    assert replayed == result.resulting_constitution
    assert exception.overrides_rule_ids == (target.rule_id,)
    assert exception.scope.section == 2
    assert exception.scope.subsection == 1
    assert exception.authority == "user_override"
    assert not resolved.conflict_report.has_blocking
    assert any(
        decision.action == "valid_exception"
        and decision.artifact_id == exception.rule_id
        for decision in resolved.decisions
    )


def test_global_or_equal_scope_exception_is_rejected():
    fixture = build_saturday_bakery_gold_fixture()
    target = next(
        rule
        for rule in fixture.constitution.rules
        if rule.rule_id == "bakery.explicit.storefront-schedule"
    )
    action = ConfirmationAction(
        action_id="action:bad-global-exception",
        decision="scoped_exception",
        artifact_type="rule",
        artifact_id="bad-global-exception",
        scope=target.scope,
        target_rule_id=target.rule_id,
        conditions=(
            StatePredicate(
                subject="bakery:wild-bread",
                predicate="always",
                operator="equals",
                expected=True,
            ),
        ),
        rationale="不应允许同scope例外",
        user_input_hash=canonical_hash("bad-global-exception"),
    )

    with pytest.raises(ValueError, match="must narrow"):
        apply_confirmation_actions(
            constitution=fixture.constitution,
            candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
            actions=(action,),
        )


def test_action_order_does_not_change_change_set_constitution_or_ledger():
    actions = (
        _candidate_action(
            "action:confirm-calendar",
            "modern-urban.calendar.seven-day-weekday-cycle",
        ),
        _candidate_action(
            "action:reject-internal-opening",
            "modern-urban.storefront.internal-activity-is-not-public-opening",
            decision="reject",
        ),
    )
    kwargs = {
        "constitution": _unconfirmed_constitution(),
        "candidate_packs": (build_modern_urban_cn_2020s_candidate_pack(),),
    }

    first = apply_confirmation_actions(actions=actions, **kwargs)
    second = apply_confirmation_actions(actions=tuple(reversed(actions)), **kwargs)

    assert first == second
    assert first.change_set.artifact_hash == second.change_set.artifact_hash
    assert first.decision_ledger.artifact_hash == second.decision_ledger.artifact_hash


def test_replay_requires_exact_base_version_and_hash():
    base = _unconfirmed_constitution()
    result = apply_confirmation_actions(
        constitution=base,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
        actions=(
            _candidate_action(
                "action:confirm-calendar-replay",
                "modern-urban.calendar.seven-day-weekday-cycle",
            ),
        ),
    )
    wrong_base = ProjectWorldConstitution(
        project_id=base.project_id,
        version="different-version",
        bound_candidate_packs=base.bound_candidate_packs,
    )

    with pytest.raises(ValueError, match="base version mismatch"):
        replay_constitution_change(
            constitution=wrong_base,
            change_set=result.change_set,
        )


def test_json_and_yaml_debug_views_are_deterministic_equivalent_projections():
    _, resolved, frame = _frame()
    view = build_world_runtime_debug_view(
        resolved=resolved,
        frame=frame,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
    )

    assert json.loads(view.render_json()) == yaml.safe_load(view.render_yaml())
    assert view.render_json() == view.render_json()
    assert view.render_yaml() == view.render_yaml()
    assert "final_text" not in view.render_json()
    assert "prompt" not in view.render_json().lower()


def test_blocking_resolver_conflict_appears_first_in_confirmation_queue():
    fixture = build_saturday_bakery_gold_fixture()
    target = next(
        rule
        for rule in fixture.constitution.rules
        if rule.rule_id == "bakery.explicit.storefront-schedule"
    )
    payload = target.model_dump()
    payload.update(
        rule_id="bakery.explicit.storefront-schedule-conflict-wr0g",
        prerequisites=(
            StatePredicate(
                subject="bakery:wild-bread",
                predicate="schedule_allows_public_opening",
                operator="equals",
                expected=False,
            ),
        ),
        provenance=ProvenanceRef(
            source_id="fixture:wr0g-conflict",
            source_type="fixture",
            source_hash=canonical_hash("wr0g-conflict"),
            producer="wr0g_test",
        ),
    )
    constitution = ProjectWorldConstitution(
        project_id=fixture.constitution.project_id,
        version=fixture.constitution.version,
        rules=(*fixture.constitution.rules, WorldRule(**payload)),
        lifecycles=fixture.constitution.lifecycles,
        bound_candidate_packs=fixture.constitution.bound_candidate_packs,
    )
    resolved = _resolved(constitution)
    frame = WorldRuntimeCompiler().compile(
        resolved=resolved,
        state_before=fixture.state_before,
        event_contract=fixture.event_contract,
    )
    view = build_world_runtime_debug_view(
        resolved=resolved,
        frame=frame,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
    )

    assert view.pending_confirmations[0].priority == "blocking"
    assert view.pending_confirmations[0].item_type == "resolver_conflict"
    assert view.conflicts[0].severity == "blocking"


def test_wr0g_artifacts_have_complete_consumers_and_no_production_import():
    registry = build_wr0g_consumer_registry()
    contracts = {item.artifact_type: item for item in registry.contracts}
    expected = {
        "ConfirmationDecisionLedger",
        "ConstitutionChangeSet",
        "WorldRuntimeDebugView",
    }
    writing_init = (ROOT / "app" / "writing" / "__init__.py").read_text(
        encoding="utf-8"
    )
    writer = (ROOT / "app" / "agents" / "writer.py").read_text(encoding="utf-8")

    assert expected.issubset(contracts)
    assert registry.orphaned_stable_fields == ()
    assert contracts["ConfirmationDecisionLedger"].retention == "permanent_audit"
    assert contracts["ConstitutionChangeSet"].retention == "permanent_audit"
    assert contracts["WorldRuntimeDebugView"].retention == "transient"
    assert "world_runtime_confirmation" not in writing_init
    assert "world_runtime_confirmation" not in writer
