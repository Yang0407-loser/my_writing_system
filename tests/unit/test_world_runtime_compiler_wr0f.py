from pathlib import Path

from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_compiler import WorldRuntimeCompiler
from app.writing.world_runtime_consumption import build_wr0f_consumer_registry
from app.writing.world_runtime_contracts import (
    CanonicalWorldState,
    ProjectWorldConstitution,
    ProvenanceRef,
    RuleScope,
    StatePredicate,
    WorldRule,
    canonical_hash,
)
from app.writing.world_runtime_kernel import build_minimal_universal_kernel
from app.writing.world_runtime_event_contracts import SubsectionEventContract
from app.writing.world_runtime_pack_modern_urban import (
    build_modern_urban_cn_2020s_candidate_pack,
)
from app.writing.world_runtime_resolver import WorldRuntimeResolver


ROOT = Path(__file__).resolve().parents[2]


def _resolved(constitution):
    return WorldRuntimeResolver().resolve(
        constitution=constitution,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
        kernel=build_minimal_universal_kernel(),
    )


def _compile(fixture=None, *, constitution=None, state=None, contract=None):
    fixture = fixture or build_saturday_bakery_gold_fixture()
    constitution = constitution or fixture.constitution
    return WorldRuntimeCompiler().compile(
        resolved=_resolved(constitution),
        state_before=state or fixture.state_before,
        event_contract=contract or fixture.event_contract,
    )


def test_bakery_runtime_frame_is_complete_and_deterministic():
    fixture = build_saturday_bakery_gold_fixture()
    first = _compile(fixture)
    second = _compile(fixture)

    assert first.status == "complete"
    assert first.frame_hash == second.frame_hash
    assert first.frame_hash == "fa6af4db3dc29da7320db478e26ad3675882ec3b3c181d4fe25645de622ca4d5"
    assert len(first.facts) == 15
    assert len(first.activated_rules) == 13
    assert len(first.activated_lifecycles) == 3
    assert len(first.transition_options) == 6
    assert first.issues == ()


def test_frame_contains_only_event_bound_facts_and_preserves_unknown():
    frame = _compile()
    fact_ids = {fact.fact_id for fact in frame.facts}

    assert "fact:clock:time" in fact_ids
    assert "fact:bakery:storefront" in fact_ids
    assert "fact:article:status" in fact_ids
    assert "fact:jiqing:article-knowledge" in fact_ids
    assert "fact:resignation:state" in fact_ids
    assert "fact:coworker:article-knowledge" not in fact_ids
    assert [(item.fact_id, item.reason) for item in frame.unknowns] == [
        (
            "fact:company:acknowledgement",
            "canonical_state_epistemic_status_unknown",
        )
    ]


def test_active_and_inactive_rules_are_not_conflated():
    frame = _compile()
    active_ids = {item.rule_id for item in frame.activated_rules}
    excluded = {
        (item.artifact_type, item.artifact_id, item.exclusion_reason)
        for item in frame.excluded_artifacts
    }

    assert len([rule_id for rule_id in active_ids if rule_id.startswith("kernel.")]) == 5
    assert "bakery.explicit.storefront-schedule" in active_ids
    assert (
        "rule",
        "modern-urban.storefront.public-opening-requires-schedule-or-exception",
        "inactive_candidate",
    ) in excluded
    candidate_rule_ids = {
        item.rule_id for item in build_modern_urban_cn_2020s_candidate_pack().rules
    }
    assert active_ids.isdisjoint(candidate_rule_ids)


def test_required_lifecycle_paths_are_compiled_in_order():
    frame = _compile()
    by_event = {}
    for option in frame.transition_options:
        by_event.setdefault(option.event_id, []).append(option)

    article = by_event["event:publish-article"]
    assert [(item.from_state, item.to_state) for item in article] == [
        ("draft", "submitted"),
        ("submitted", "published"),
    ]
    assert article[0].availability == "currently_applicable"
    assert article[1].availability == "requires_prior_transition"
    assert article[1].preceding_transition_ids == (article[0].transition_id,)

    knowledge = by_event["event:share-with-jiqing"]
    assert [(item.from_state, item.to_state) for item in knowledge] == [
        ("unknown", "available"),
        ("available", "reached"),
        ("reached", "perceived"),
    ]


def test_compiler_inserts_legal_bridge_without_deleting_must_event():
    fixture = build_saturday_bakery_gold_fixture()
    requirement = next(
        item
        for item in fixture.event_contract.requirements
        if item.event_id == "event:publish-article"
    )
    publish_transition = (
        "bakery.confirmed.modern-urban.lifecycle.publication.publish"
    )
    binding = requirement.runtime_binding.model_copy(
        update={"required_transition_ids": (publish_transition,)}
    )
    requirement = requirement.model_copy(update={"runtime_binding": binding})
    contract = SubsectionEventContract(
        contract_id="event-contract:bridge-only",
        project_id=fixture.event_contract.project_id,
        section=2,
        subsection=1,
        requirements=(requirement,),
        provenance=fixture.event_contract.provenance,
    )

    frame = _compile(fixture, contract=contract)
    boundary = frame.event_boundaries[0]
    options = frame.transition_options

    assert frame.status == "complete"
    assert boundary.status == "requires_bridge"
    assert boundary.transition_ids == (publish_transition,)
    assert boundary.bridge_transition_ids == (
        "bakery.confirmed.modern-urban.lifecycle.publication.submit",
    )
    assert [item.required_by_event for item in options] == [False, True]
    assert options[-1].transition_id == publish_transition


def test_unrelated_rule_does_not_change_runtime_frame_or_hash():
    fixture = build_saturday_bakery_gold_fixture()
    baseline = _compile(fixture)
    irrelevant = WorldRule(
        rule_id="fixture:finance-credit-default",
        semantic_key="finance.credit.default",
        kind="default_assumption",
        authority="project_explicit",
        enforcement="suggest",
        scope=RuleScope(project_id=fixture.constitution.project_id),
        prerequisites=(
            StatePredicate(
                subject="bank-account",
                predicate="credit_check_complete",
                operator="equals",
                expected=True,
            ),
        ),
        provenance=ProvenanceRef(
            source_id="fixture:irrelevant-finance-rule",
            source_type="fixture",
            source_hash=canonical_hash("irrelevant-finance-rule"),
            producer="wr0f_test",
        ),
        version="1",
    )
    constitution = ProjectWorldConstitution(
        project_id=fixture.constitution.project_id,
        version=fixture.constitution.version,
        rules=(*fixture.constitution.rules, irrelevant),
        lifecycles=fixture.constitution.lifecycles,
        bound_candidate_packs=fixture.constitution.bound_candidate_packs,
    )

    mutated = _compile(fixture, constitution=constitution)

    assert mutated == baseline
    assert mutated.frame_hash == baseline.frame_hash


def test_fact_order_does_not_change_runtime_frame():
    fixture = build_saturday_bakery_gold_fixture()
    reordered = CanonicalWorldState(
        project_id=fixture.state_before.project_id,
        revision=fixture.state_before.revision,
        facts=tuple(reversed(fixture.state_before.facts)),
    )

    assert _compile(fixture, state=reordered) == _compile(fixture)


def test_missing_bound_fact_returns_partial_frame_with_explicit_issue():
    fixture = build_saturday_bakery_gold_fixture()
    partial_state = CanonicalWorldState(
        project_id=fixture.state_before.project_id,
        revision=fixture.state_before.revision,
        facts=tuple(
            fact
            for fact in fixture.state_before.facts
            if fact.fact_id != "fact:company:acknowledgement"
        ),
    )

    frame = _compile(fixture, state=partial_state)

    assert frame.status == "partial"
    assert {item.code for item in frame.issues} == {"missing_bound_fact"}
    assert frame.issues[0].artifact_ids == ("fact:company:acknowledgement",)


def test_blocking_resolver_conflict_gates_compilation():
    fixture = build_saturday_bakery_gold_fixture()
    schedule = next(
        rule
        for rule in fixture.constitution.rules
        if rule.rule_id == "bakery.explicit.storefront-schedule"
    )
    payload = schedule.model_dump()
    payload.update(
        rule_id="bakery.explicit.storefront-schedule-conflict",
        prerequisites=(
            StatePredicate(
                subject="bakery:wild-bread",
                predicate="schedule_allows_public_opening",
                operator="equals",
                expected=False,
            ),
        ),
        provenance=ProvenanceRef(
            source_id="fixture:conflicting-schedule",
            source_type="fixture",
            source_hash=canonical_hash("conflicting-schedule"),
            producer="wr0f_test",
        ),
    )
    conflict = WorldRule(**payload)
    constitution = ProjectWorldConstitution(
        project_id=fixture.constitution.project_id,
        version=fixture.constitution.version,
        rules=(*fixture.constitution.rules, conflict),
        lifecycles=fixture.constitution.lifecycles,
        bound_candidate_packs=fixture.constitution.bound_candidate_packs,
    )

    frame = _compile(fixture, constitution=constitution)

    assert frame.status == "blocked"
    assert {item.code for item in frame.issues} == {
        "blocking_resolution_conflict"
    }
    assert frame.activated_rules == ()
    assert frame.transition_options == ()


def test_handover_is_not_a_compiler_input_or_frame_dependency():
    with_handover = build_saturday_bakery_gold_fixture(
        include_handover_projection=True
    )
    without_handover = build_saturday_bakery_gold_fixture(
        include_handover_projection=False
    )

    assert _compile(with_handover) == _compile(without_handover)


def test_compiler_does_not_mutate_inputs():
    fixture = build_saturday_bakery_gold_fixture()
    resolved = _resolved(fixture.constitution)
    hashes_before = (
        resolved.resolved_hash,
        fixture.state_before.artifact_hash,
        fixture.event_contract.artifact_hash,
    )

    WorldRuntimeCompiler().compile(
        resolved=resolved,
        state_before=fixture.state_before,
        event_contract=fixture.event_contract,
    )

    assert hashes_before == (
        resolved.resolved_hash,
        fixture.state_before.artifact_hash,
        fixture.event_contract.artifact_hash,
    )


def test_compiler_has_no_production_writer_or_package_facade_import():
    writing_init = (ROOT / "app" / "writing" / "__init__.py").read_text(
        encoding="utf-8"
    )
    writer = (ROOT / "app" / "agents" / "writer.py").read_text(encoding="utf-8")

    assert "world_runtime_compiler" not in writing_init
    assert "world_runtime_compiler" not in writer


def test_scene_runtime_frame_has_complete_transient_consumption_contract():
    registry = build_wr0f_consumer_registry()
    contract = next(
        item
        for item in registry.contracts
        if item.artifact_type == "SceneRuntimeFrame"
    )
    frame = _compile()

    assert set(contract.stable_fields) == set(type(frame).model_fields)
    assert contract.retention == "transient"
    assert contract.owns_semantics is False
    assert registry.orphaned_stable_fields == ()
    consumers = {item.consumer: item for item in contract.consumers}
    assert consumers["wr0f_regression_suite"].required is True
    assert consumers["writer_runtime_renderer"].required is False
    assert consumers["writer_runtime_renderer"].fallback == (
        "shadow_only_no_prompt_injection"
    )
