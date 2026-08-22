from pathlib import Path

import pytest
from pydantic import ValidationError

from app.writing.world_runtime_consumption import (
    ArtifactConsumerContract,
    ConsumptionReceipt,
    ConsumerBinding,
    RuntimeArtifactConsumerRegistry,
    build_wr0a_consumer_registry,
)
from app.writing.world_runtime_contracts import (
    CandidatePack,
    CanonicalWorldState,
    Lifecycle,
    LifecycleTransition,
    NarrativePolicy,
    NarrativePreference,
    ProjectWorldConstitution,
    ProvenanceRef,
    StatePredicate,
    TransitionValidation,
    WorldFact,
    WorldRule,
)


ROOT = Path(__file__).resolve().parents[2]


def _source(source_id: str = "fixture") -> ProvenanceRef:
    return ProvenanceRef(
        source_id=source_id,
        source_type="fixture",
        source_hash=f"hash:{source_id}",
        producer="wr0a_fixture",
    )


def _rule(**overrides) -> WorldRule:
    payload = {
        "rule_id": "rule:publication_requires_delivery",
        "semantic_key": "article.publication",
        "kind": "precondition",
        "authority": "project_explicit",
        "enforcement": "block",
        "prerequisites": (
            StatePredicate(
                subject="article",
                predicate="delivery_status",
                operator="equals",
                expected="published",
            ),
        ),
        "provenance": _source("world-setting:publication"),
        "version": "1",
    }
    payload.update(overrides)
    if payload["authority"] == "pack_candidate":
        payload.setdefault("activation_enforcement", "suggest")
    return WorldRule(**payload)


def _lifecycle(**overrides) -> Lifecycle:
    payload = {
        "lifecycle_id": "article-publication",
        "semantic_key": "article.publication.lifecycle",
        "states": ("draft", "published", "distributed"),
        "initial_state": "draft",
        "transitions": (
            LifecycleTransition(
                transition_id="publish",
                from_state="draft",
                to_state="published",
            ),
            LifecycleTransition(
                transition_id="distribute",
                from_state="published",
                to_state="distributed",
            ),
        ),
        "terminal_states": ("distributed",),
        "authority": "project_explicit",
        "enforcement": "block",
        "provenance": _source("world-setting:article-lifecycle"),
        "version": "1",
    }
    payload.update(overrides)
    if payload["authority"] == "pack_candidate":
        payload.setdefault("activation_enforcement", "suggest")
    return Lifecycle(**payload)


def _fact(**overrides) -> WorldFact:
    payload = {
        "fact_id": "fact:article:status",
        "subject": "article",
        "predicate": "publication_status",
        "value": "draft",
        "epistemic_status": "confirmed_true",
        "authority": "project_explicit",
        "provenance": _source("project-state:article"),
        "revision": 1,
    }
    payload.update(overrides)
    return WorldFact(**payload)


def test_pack_candidates_are_structurally_inactive():
    candidate_rule = _rule(
        authority="pack_candidate",
        enforcement="inactive",
        rule_id="candidate:breakthrough-cost",
    )
    candidate_lifecycle = _lifecycle(
        authority="pack_candidate",
        enforcement="inactive",
        lifecycle_id="candidate:breakthrough",
    )
    preference = NarrativePreference(
        preference_id="preference:breakthrough-has-cost",
        content="突破通常应体现代价",
        authority="pack_candidate",
        influence="inactive",
        provenance=_source("pack:xianxia"),
        version="1",
    )
    pack = CandidatePack(
        pack_id="xianxia-conventions",
        version="1",
        rules=(candidate_rule,),
        lifecycles=(candidate_lifecycle,),
        narrative_preferences=(preference,),
    )
    assert pack.rules[0].enforcement == "inactive"
    assert pack.narrative_preferences[0].influence == "inactive"

    with pytest.raises(ValidationError, match="pack candidate rules must remain inactive"):
        _rule(authority="pack_candidate", enforcement="warn")


def test_inferred_or_extracted_rules_cannot_block():
    with pytest.raises(ValidationError, match="cannot block"):
        _rule(authority="model_inferred", enforcement="block")
    with pytest.raises(ValidationError, match="cannot block"):
        _rule(authority="text_extracted", enforcement="block")


def test_constitution_rejects_candidates_and_current_facts():
    constitution = ProjectWorldConstitution(
        project_id="project-1",
        version="1",
        rules=(_rule(),),
        lifecycles=(_lifecycle(),),
    )
    assert constitution.rules[0].authority == "project_explicit"

    with pytest.raises(ValidationError, match="constitution rules"):
        ProjectWorldConstitution(
            project_id="project-1",
            version="1",
            rules=(_rule(authority="pack_candidate", enforcement="inactive"),),
        )
    with pytest.raises(ValidationError):
        ProjectWorldConstitution(
            project_id="project-1",
            version="1",
            facts=(_fact(),),
        )


def test_unknown_is_not_false_or_an_assumed_value():
    unknown = _fact(
        fact_id="fact:voice-transmission",
        predicate="can_voice_transmit",
        value=None,
        epistemic_status="unknown",
    )
    state = CanonicalWorldState(project_id="project-1", revision=1, facts=(unknown,))
    assert state.facts[0].epistemic_status == "unknown"
    assert state.facts[0].value is None

    with pytest.raises(ValidationError, match="unknown facts"):
        _fact(value=False, epistemic_status="unknown")


def test_proposed_or_future_fact_cannot_enter_canonical_state():
    proposed = _fact(
        authority="model_inferred",
        epistemic_status="proposed",
        value="published",
    )
    with pytest.raises(ValidationError, match="proposed facts"):
        CanonicalWorldState(project_id="project-1", revision=1, facts=(proposed,))
    with pytest.raises(ValidationError, match="fact revision"):
        CanonicalWorldState(
            project_id="project-1",
            revision=1,
            facts=(_fact(revision=2),),
        )


def test_lifecycle_is_first_class_and_rejects_undeclared_states():
    lifecycle = _lifecycle()
    assert lifecycle.initial_state == "draft"
    assert lifecycle.transitions[1].to_state == "distributed"

    with pytest.raises(ValidationError, match="declared states"):
        _lifecycle(
            transitions=(
                LifecycleTransition(
                    transition_id="invalid",
                    from_state="draft",
                    to_state="commented",
                ),
            )
        )


def test_narrative_policy_is_not_a_world_rule_container():
    preference = NarrativePreference(
        preference_id="preference:cost",
        content="突破场景通常表现出代价",
        authority="project_explicit",
        influence="suggest",
        provenance=_source("user:narrative-policy"),
        version="1",
    )
    policy = NarrativePolicy(
        project_id="project-1", version="1", preferences=(preference,)
    )
    assert policy.preferences[0].influence == "suggest"
    with pytest.raises(ValidationError):
        NarrativePolicy(
            project_id="project-1",
            version="1",
            rules=(_rule(),),
        )


def test_transition_validation_preserves_unresolved_and_exception_outcomes():
    unresolved = TransitionValidation(
        validation_id="validation-1",
        outcome="unresolved",
        unresolved_fact_ids=("fact:voice-transmission",),
    )
    assert unresolved.outcome != "invalid"
    with pytest.raises(ValidationError, match="unresolved facts"):
        TransitionValidation(validation_id="validation-2", outcome="unresolved")
    with pytest.raises(ValidationError, match="exception rule"):
        TransitionValidation(
            validation_id="validation-3", outcome="valid_with_exception"
        )


def test_wr0a_registry_has_no_orphaned_stable_fields_or_double_authority():
    registry = build_wr0a_consumer_registry()
    assert registry.orphaned_stable_fields == ()
    assert {item.artifact_type for item in registry.contracts} == {
        "CandidatePack",
        "ProjectWorldConstitution",
        "CanonicalWorldState",
        "NarrativePolicy",
    }
    assert registry.registry_hash == build_wr0a_consumer_registry().registry_hash

    artifact_models = {
        "CandidatePack": CandidatePack,
        "ProjectWorldConstitution": ProjectWorldConstitution,
        "CanonicalWorldState": CanonicalWorldState,
        "NarrativePolicy": NarrativePolicy,
    }
    for contract in registry.contracts:
        assert set(contract.stable_fields) == set(
            artifact_models[contract.artifact_type].model_fields
        )


def test_registry_rejects_orphaned_fields_and_semantic_double_authority():
    with pytest.raises(ValidationError, match="orphaned stable fields"):
        ArtifactConsumerContract(
            artifact_type="Broken",
            producer="fixture",
            authority="fixture",
            owns_semantics=True,
            semantic_keys=("world_rules",),
            stable_fields=("used", "orphaned"),
            consumers=(
                ConsumerBinding(
                    consumer="fixture",
                    stage="debug_api",
                    fields=("used",),
                    fallback="none",
                ),
            ),
            retention="transient",
        )

    first = next(
        item
        for item in build_wr0a_consumer_registry().contracts
        if item.artifact_type == "ProjectWorldConstitution"
    )
    duplicate = first.model_copy(update={"artifact_type": "SecondConstitution"})
    with pytest.raises(ValidationError, match="semantic authority conflict"):
        RuntimeArtifactConsumerRegistry(contracts=(first, duplicate))


def test_consumption_receipt_requires_real_consumption_or_named_fallback():
    receipt = ConsumptionReceipt(
        artifact_type="CanonicalWorldState",
        artifact_hash="state-hash",
        consumer="runtime_compiler",
        stage="runtime_compile",
        consumed_fields=("revision", "facts"),
        outcome="consumed",
    )
    assert receipt.receipt_hash == receipt.receipt_hash
    with pytest.raises(ValidationError, match="consumed_fields"):
        ConsumptionReceipt(
            artifact_type="CanonicalWorldState",
            artifact_hash="state-hash",
            consumer="runtime_compiler",
            stage="runtime_compile",
            outcome="consumed",
        )
    with pytest.raises(ValidationError, match="fallback_code"):
        ConsumptionReceipt(
            artifact_type="CanonicalWorldState",
            artifact_hash="state-hash",
            consumer="runtime_compiler",
            stage="runtime_compile",
            outcome="fallback",
        )


def test_wr0a_contracts_are_not_imported_by_production_facades():
    writing_init = (ROOT / "app" / "writing" / "__init__.py").read_text("utf-8")
    writer = (ROOT / "app" / "agents" / "writer.py").read_text("utf-8")
    assert "world_runtime_contracts" not in writing_init
    assert "world_runtime_consumption" not in writing_init
    assert "world_runtime_contracts" not in writer
    assert "world_runtime_consumption" not in writer
