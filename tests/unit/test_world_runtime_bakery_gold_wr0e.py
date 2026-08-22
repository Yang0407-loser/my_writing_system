from pathlib import Path

import pytest
from pydantic import ValidationError

from app.writing.world_runtime_bakery_gold import (
    BAKERY_PROJECT_ID,
    SaturdayBakeryGoldFixture,
    build_saturday_bakery_gold_fixture,
)
from app.writing.world_runtime_consumption import build_wr0e_consumer_registry


ROOT = Path(__file__).resolve().parents[2]


def _facts(state):
    return {fact.fact_id: fact for fact in state.facts}


def test_gold_fixture_and_artifact_hashes_are_frozen():
    fixture = build_saturday_bakery_gold_fixture()

    assert fixture.artifact_hash == "a0f96021b7715ce828478b931655d31f252534da54543773f34b4640d6fa08f0"
    assert fixture.output_hash == "a1fd103f17b0dafe2e6cd05eedc969fd2ed62812a4bf499fb1ab2a90d03c99d1"
    assert fixture.constitution.artifact_hash == "dd54f953ebad575cf63b894939ddee3fc0589b74c2359182109753639d1bf87a"
    assert fixture.state_before.artifact_hash == "336279f350faea539e76758a8343bc6455af5307ce6c2aada10036b03d9386c3"
    assert fixture.state_after.artifact_hash == "8f1c964199e0be1dad8367f1eed4df80d268db40f63ec9e65894d905fb0d27dd"


def test_authority_layers_remain_separate_in_gold_fixture():
    fixture = build_saturday_bakery_gold_fixture()

    assert fixture.constitution.bound_candidate_packs == (
        "modern-urban-cn-2020s@1.0.0",
    )
    assert all(
        item.authority == "project_explicit"
        for item in (*fixture.constitution.rules, *fixture.constitution.lifecycles)
    )
    assert all(
        fact.authority != "pack_candidate" for fact in fixture.state_before.facts
    )
    assert all(
        fact.authority == "model_inferred"
        and fact.epistemic_status == "proposed"
        for fact in fixture.model_inferred_candidates
    )
    assert not {
        fact.fact_id for fact in fixture.model_inferred_candidates
    } & {fact.fact_id for fact in fixture.state_before.facts}


def test_before_state_freezes_time_business_publication_and_resignation():
    facts = _facts(build_saturday_bakery_gold_fixture().state_before)

    assert facts["fact:clock:weekday"].value == "saturday"
    assert facts["fact:clock:time"].value == "04:20"
    assert facts["fact:bakery:opens-at"].value == "06:00"
    assert facts["fact:bakery:storefront"].value == "closed"
    assert facts["fact:article:status"].value == "draft"
    assert facts["fact:resignation:state"].value == "private_draft"
    assert facts["fact:company:acknowledgement"].epistemic_status == "unknown"
    assert facts["fact:company:acknowledgement"].value is None


def test_event_contract_maps_every_must_event_to_accepted_changes():
    fixture = build_saturday_bakery_gold_fixture()
    accepted = set(fixture.validation_result.accepted_change_ids)

    assert len(fixture.event_contract.requirements) == 4
    assert all(requirement.required for requirement in fixture.event_contract.requirements)
    assert all(
        set(expectation.expected_change_ids).issubset(accepted)
        for expectation in fixture.event_change_expectations
    )


def test_final_text_evidence_has_exact_hash_bound_spans():
    fixture = build_saturday_bakery_gold_fixture()
    final_evidence = [
        item for item in fixture.evidence if item.source_type == "final_text"
    ]

    assert len(final_evidence) == 7
    for item in final_evidence:
        assert item.source_hash == fixture.output_hash
        assert fixture.final_text[item.start:item.end] == item.excerpt


def test_gold_validation_explains_all_four_known_illegal_changes():
    fixture = build_saturday_bakery_gold_fixture()
    rejected = {
        item.change_id: item
        for item in fixture.validation_result.items
        if item.outcome == "invalid"
    }

    assert set(rejected) == {
        "change:storefront-public-open",
        "change:public-comment-increment",
        "change:employment-terminated",
        "change:coworker-knows-article",
    }
    assert all(item.rule_ids and item.reasons for item in rejected.values())
    assert "04:20早于06:00对外营业时间" in rejected[
        "change:storefront-public-open"
    ].reasons
    assert "私信回复不是文章公共评论" in rejected[
        "change:public-comment-increment"
    ].reasons
    assert any(
        "不能跳到terminated" in reason
        for reason in rejected["change:employment-terminated"].reasons
    )
    assert "发送对象是季晴，没有同事的传播或感知路径" in rejected[
        "change:coworker-knows-article"
    ].reasons


def test_accepted_lifecycle_changes_follow_declared_intermediate_transitions():
    fixture = build_saturday_bakery_gold_fixture()
    accepted = set(fixture.validation_result.accepted_change_ids)
    changes = [
        change
        for change in fixture.proposed_delta.changes
        if change.change_id in accepted and change.lifecycle_id
    ]

    assert [
        (change.before_value, change.after_value)
        for change in changes
        if "article" in change.change_id
    ] == [("draft", "submitted"), ("submitted", "published")]
    assert [
        (change.before_value, change.after_value)
        for change in changes
        if "jiqing" in change.change_id
    ] == [
        ("unknown", "available"),
        ("available", "reached"),
        ("reached", "perceived"),
    ]
    assert [
        (change.before_value, change.after_value)
        for change in changes
        if "resignation" in change.change_id
    ] == [("private_draft", "delivered")]


def test_commit_contains_only_valid_changes_and_preserves_unknowns():
    fixture = build_saturday_bakery_gold_fixture()
    before = _facts(fixture.state_before)
    after = _facts(fixture.state_after)
    committed_ids = {change.change_id for change in fixture.committed_delta.changes}

    assert committed_ids == set(fixture.validation_result.accepted_change_ids)
    assert committed_ids.isdisjoint(fixture.validation_result.rejected_change_ids)
    assert fixture.state_before.revision == 7
    assert fixture.state_after.revision == 8
    assert after["fact:bakery:storefront"].value == "closed"
    assert after["fact:article:comment-count"].value == 0
    assert after["fact:employment:state"].value == "employed"
    assert after["fact:article:status"].value == "published"
    assert after["fact:jiqing:article-knowledge"].value == "perceived"
    assert after["fact:resignation:state"].value == "delivered"
    for fact_id in (
        "fact:company:acknowledgement",
        "fact:coworker:article-knowledge",
    ):
        assert after[fact_id].epistemic_status == "unknown"
        assert after[fact_id].value is None
        assert after[fact_id] == before[fact_id]


def test_removing_handover_does_not_change_authoritative_state_or_commit():
    with_handover = build_saturday_bakery_gold_fixture(
        include_handover_projection=True
    )
    without_handover = build_saturday_bakery_gold_fixture(
        include_handover_projection=False
    )

    assert with_handover.handover_projection
    assert without_handover.handover_projection is None
    assert with_handover.state_before == without_handover.state_before
    assert with_handover.committed_delta == without_handover.committed_delta
    assert with_handover.state_after == without_handover.state_after


def test_output_hash_mutation_breaks_gold_chain():
    fixture = build_saturday_bakery_gold_fixture()
    payload = fixture.model_dump()
    payload["final_text"] += "篡改"

    with pytest.raises(ValidationError, match="final text hash mismatch"):
        SaturdayBakeryGoldFixture(**payload)


def test_rejected_change_cannot_be_smuggled_into_commit():
    fixture = build_saturday_bakery_gold_fixture()
    payload = fixture.model_dump()
    rejected = next(
        change
        for change in payload["proposed_delta"]["changes"]
        if change["change_id"] == "change:employment-terminated"
    )
    payload["committed_delta"]["changes"] = (
        *payload["committed_delta"]["changes"],
        rejected,
    )

    with pytest.raises(ValidationError, match="accepted changes only"):
        SaturdayBakeryGoldFixture(**payload)


def test_fixture_has_no_production_writer_or_package_facade_import():
    writing_init = (ROOT / "app" / "writing" / "__init__.py").read_text(
        encoding="utf-8"
    )
    writer = (ROOT / "app" / "agents" / "writer.py").read_text(encoding="utf-8")

    assert "world_runtime_bakery_gold" not in writing_init
    assert "world_runtime_bakery_gold" not in writer
    assert BAKERY_PROJECT_ID not in writer


def test_wr0e_gold_artifacts_have_explicit_transient_consumers():
    registry = build_wr0e_consumer_registry()
    contracts = {item.artifact_type: item for item in registry.contracts}
    expected = {
        "SubsectionEventContract",
        "ProposedStateDelta",
        "GoldValidationResult",
        "GoldCommittedStateDelta",
        "SaturdayBakeryGoldFixture",
    }

    assert expected.issubset(contracts)
    assert registry.orphaned_stable_fields == ()
    for artifact_type in expected:
        contract = contracts[artifact_type]
        assert contract.retention == "transient"
        assert contract.owns_semantics is False


def test_wr0e_registry_stable_fields_match_typed_models():
    fixture = build_saturday_bakery_gold_fixture()
    registry = build_wr0e_consumer_registry()
    contracts = {item.artifact_type: item for item in registry.contracts}
    artifacts = {
        "SubsectionEventContract": fixture.event_contract,
        "ProposedStateDelta": fixture.proposed_delta,
        "GoldValidationResult": fixture.validation_result,
        "GoldCommittedStateDelta": fixture.committed_delta,
        "SaturdayBakeryGoldFixture": fixture,
    }

    for artifact_type, artifact in artifacts.items():
        assert set(contracts[artifact_type].stable_fields) == set(
            type(artifact).model_fields
        )
