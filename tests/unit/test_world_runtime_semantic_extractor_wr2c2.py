import json

from experiments.world_runtime_writer_canary.delta_shadow_wr2b import validate_delta_v2
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c2 import (
    build_messages,
    parse_semantic_response,
)


def _response(events):
    return json.dumps({"events": events}, ensure_ascii=False)


def _event(**updates):
    value = {
        "change_type": "object_state",
        "subject": "object:green-bean-soup-bowl",
        "predicate": "content_state",
        "after_value": "empty",
        "actor": "character:lin-wan",
        "mechanism": "explicit actor",
        "event_id": None,
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "把绿豆汤倒进水槽", "occurrence": 1}],
    }
    value.update(updates)
    return value


def test_prompt_adds_only_three_missing_type_examples_and_composite_rule():
    messages = build_messages(text="她没有移动。", state_variant="after_augmented")
    prompt = messages[1]["content"]

    assert "unsourced_project_fact" in prompt
    assert "repeated_completed_event" in prompt
    assert "location_state" in prompt
    assert "content_state=clean_and_stored" in prompt
    assert '"expected_validation":' not in prompt


def test_local_projector_owns_institutional_actor_and_restores_validator_chain():
    text = "人事系统回信确认辞职生效，随后劳动关系正式结束。"
    events = [
        _event(
            change_type="resignation_acknowledgement",
            subject="company:lin-wan",
            predicate="resignation_acknowledged",
            after_value=True,
            actor="company:lin-wan",
            mechanism="institutional_reply",
            evidence=[{"excerpt": "人事系统回信确认辞职生效", "occurrence": 1}],
        ),
        _event(
            change_type="employment_state",
            subject="employment:lin-wan",
            predicate="status",
            after_value="ended",
            actor="character:lin-wan",
            mechanism="acknowledged_effective_resignation",
            evidence=[{"excerpt": "劳动关系正式结束", "occurrence": 1}],
        ),
    ]
    artifact = parse_semantic_response(
        text=text,
        response_text=_response(events),
        sample_id="WR2C2-E-01",
        scene_id="adversarial-employment-transition",
        state_variant="after",
    )
    validation = validate_delta_v2(artifact.delta)

    assert artifact.delta.changes[0].actor == "company:hr-system"
    assert validation.accepted_change_ids == tuple(change.change_id for change in artifact.delta.changes)


def test_object_mechanism_alias_is_canonicalized_locally():
    artifact = parse_semantic_response(
        text="林晚把绿豆汤倒进水槽。",
        response_text=_response([_event()]),
        sample_id="WR2C2-O-01",
        scene_id="adversarial-object-and-repeat",
        state_variant="after_augmented",
    )

    assert artifact.projected_event_count == 1
    assert artifact.delta.changes[0].mechanism == "actor_pours_out"


def test_state_diff_suppresses_object_location_that_did_not_change():
    text = "她把空碗留在茶几上。"
    artifact = parse_semantic_response(
        text=text,
        response_text=_response([_event(
            predicate="location_state",
            after_value="lin-wan-home:coffee-table",
            mechanism="explicit_action",
            evidence=[{"excerpt": "空碗留在茶几上", "occurrence": 1}],
        )]),
        sample_id="WR2C2-O-NOOP",
        scene_id="adversarial-object-and-repeat",
        state_variant="after_augmented",
    )

    assert artifact.delta.changes == ()
    assert [item.reason for item in artifact.dropped_events] == ["no_state_change"]


def test_split_clean_and_store_is_coalesced_into_one_atomic_object_change():
    text = "没人进过屋，回来时碗已经洗净，收在厨房橱柜里。"
    events = [
        _event(
            after_value="empty",
            actor="unknown",
            mechanism="missing actor or event",
            evidence=[{"excerpt": "碗已经洗净", "occurrence": 1}],
        ),
        _event(
            predicate="location_state",
            after_value="lin-wan-home:kitchen-cabinet",
            actor="unknown",
            mechanism="missing_actor_or_event",
            evidence=[{"excerpt": "收在厨房橱柜里", "occurrence": 1}],
        ),
    ]
    artifact = parse_semantic_response(
        text=text,
        response_text=_response(events),
        sample_id="WR2C2-O-COMPOSITE",
        scene_id="adversarial-object-and-repeat",
        state_variant="after_augmented",
    )
    validation = validate_delta_v2(artifact.delta)

    assert artifact.projected_event_count == 1
    change = artifact.delta.changes[0]
    assert (change.predicate, change.after_value, change.mechanism) == (
        "content_state", "clean_and_stored", "missing_actor_or_event"
    )
    assert len(change.evidence_ids) == 2
    assert validation.rejected_change_ids == (change.change_id,)
    assert any(item.reason == "coalesced_into_object_composite" for item in artifact.dropped_events)
