import json

import pytest

from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from experiments.world_runtime_writer_canary.delta_shadow_wr2c5 import validate_delta_v5
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c513r4 import (
    parse_semantic_response,
)
from experiments.world_runtime_writer_canary.state_commit_adapter_wr2c6 import (
    to_committable,
)


_TYPES = (
    "storefront_public_sale", "storefront_public_handoff", "storefront_operation_state",
    "knowledge_state", "resignation_acknowledgement", "unsourced_project_fact",
    "object_state", "repeated_completed_event", "employment_state", "publication_state",
    "resignation_delivery", "resignation_personal_record", "clock_state", "location_state",
)


def _all_false():
    return [
        {
            "change_type": change_type,
            "occurred": False,
            "after_value": None,
            "mode": "actual",
            "epistemic": "asserted",
            "evidence": [],
        }
        for change_type in _TYPES
    ]


def _parse(text, judgments, sample_id="ADAPTER-U-01"):
    return parse_semantic_response(
        text=text,
        response_text=json.dumps({"judgments": judgments}, ensure_ascii=False),
        sample_id=sample_id,
        scene_id="adapter-chain-canary",
        state_variant="before",
        base_revision=7,
    )


def _gold():
    return build_saturday_bakery_gold_fixture()


def test_multi_clock_before_values_are_chained_and_commit_succeeds():
    gold = _gold()
    text = "四点二十三分，林晚看了一眼挂钟；四点五十分，烤箱预热完成。"
    artifact = _parse(text, _all_false())
    validation = validate_delta_v5(artifact.delta)
    assert len(validation.accepted_change_ids) == 2

    delta, committable_validation = to_committable(
        artifact.delta,
        validation,
        project_id=gold.state_before.project_id,
        base_state=gold.state_before,
        before_state=gold.state_before,
    )
    assert [change.before_value for change in delta.changes] == ["04:20", "04:23"]
    assert [change.after_value for change in delta.changes] == ["04:23", "04:50"]

    committed = WorldRuntimeStateCommitter().commit(
        idempotency_key="adapter:multi-clock",
        before=gold.state_before,
        delta=delta,
        validation=committable_validation,
        final_text_hash=artifact.output_hash,
    )
    clock = next(
        fact for fact in committed.after.facts
        if fact.subject == "world_clock" and fact.predicate == "time"
    )
    assert clock.value == "04:50"
    assert len(committed.ledger.entries) == 2


def test_creation_chain_before_is_rewritten_and_commits():
    gold = _gold()
    text = "林晚把碗里的绿豆汤倒进水池，然后把碗洗净放进柜子。"
    judgments = _all_false()
    judgments[_TYPES.index("object_state")] = {
        "change_type": "object_state",
        "occurred": True,
        "after_value": "empty",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "林晚把碗里的绿豆汤倒进水池", "occurrence": 1}],
    }
    judgments.append({
        "change_type": "object_state",
        "occurred": True,
        "after_value": "clean_and_stored",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "把碗洗净放进柜子", "occurrence": 1}],
    })
    artifact = _parse(text, judgments, sample_id="ADAPTER-U-02")
    validation = validate_delta_v5(artifact.delta)
    assert len(validation.accepted_change_ids) == 2

    delta, committable_validation = to_committable(
        artifact.delta,
        validation,
        project_id=gold.state_before.project_id,
        base_state=gold.state_before,
        before_state=gold.state_before,
    )
    assert delta.changes[0].before_value is None
    assert delta.changes[0].before_epistemic_status == "unknown"
    assert delta.changes[1].before_value == "empty"
    assert delta.changes[1].before_epistemic_status == "confirmed_true"

    committed = WorldRuntimeStateCommitter().commit(
        idempotency_key="adapter:creation-chain",
        before=gold.state_before,
        delta=delta,
        validation=committable_validation,
        final_text_hash=artifact.output_hash,
    )
    fact = next(
        item for item in committed.after.facts
        if item.fact_id == "fact:object-green-bean-soup-bowl:content_state"
    )
    assert fact.value == "clean_and_stored"
    assert len(committed.ledger.entries) == 2
    assert {entry.fact_id for entry in committed.ledger.entries} == {fact.fact_id}


def test_hallucinated_before_value_is_rejected():
    gold = _gold()
    text = "四点二十三分，林晚看了一眼挂钟；四点五十分，烤箱预热完成。"
    artifact = _parse(text, _all_false())
    target = artifact.delta.changes[1]
    broken = target.model_copy(update={"before_value": "never-was"})
    broken_delta = artifact.delta.model_copy(update={
        "changes": tuple(
            broken if change.change_id == target.change_id else change
            for change in artifact.delta.changes
        ),
    })
    validation = validate_delta_v5(broken_delta)
    with pytest.raises(ValueError, match="before value mismatch"):
        to_committable(
            broken_delta,
            validation,
            project_id=gold.state_before.project_id,
            base_state=gold.state_before,
            before_state=gold.state_before,
        )


def test_without_states_keeps_declared_before_values():
    gold = _gold()
    text = "四点二十三分，林晚看了一眼挂钟；四点五十分，烤箱预热完成。"
    artifact = _parse(text, _all_false())
    validation = validate_delta_v5(artifact.delta)
    delta, _ = to_committable(
        artifact.delta,
        validation,
        project_id=gold.state_before.project_id,
    )
    assert [change.before_value for change in delta.changes] == ["04:20", "04:20"]
