import json

from experiments.world_runtime_writer_canary.delta_shadow_wr2c5 import validate_delta_v5
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c513 import (
    build_messages,
    parse_semantic_response,
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


def _judgment(**updates):
    value = {
        "change_type": "clock_state",
        "occurred": True,
        "after_value": "06:12",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "六点十二分", "occurrence": 1}],
    }
    value.update(updates)
    return value


def _parse(text, items, sample_id="WR2C513-U-01", state_variant="before", scene_id="adversarial-storefront-hours"):
    return parse_semantic_response(
        text=text,
        response_text=json.dumps({"judgments": items}, ensure_ascii=False),
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
    )


def test_clock_events_are_ordered_by_time():
    text = "周六清晨五点四十六分，林晚推开面包店操作间的门走进去；六点十二分，她把第一炉可颂放进烤箱。"
    artifact = _parse(
        text,
        [
            _judgment(after_value="06:12", evidence=[{"excerpt": "六点十二分", "occurrence": 1}]),
            _judgment(
                change_type="location_state",
                after_value="操作间",
                evidence=[
                    {"excerpt": "林晚推开面包店操作间的门走进去", "occurrence": 1},
                ],
            ),
        ] + [item for item in _all_false() if item["change_type"] not in {"clock_state", "location_state"}],
    )
    clocks = [change.after_value for change in artifact.delta.changes if change.change_type == "clock_state"]
    assert clocks == ["05:46", "06:12"]
    validation = validate_delta_v5(artifact.delta)
    assert len(validation.rejected_change_ids) == 0


def test_publication_is_subsumed_by_repeat():
    text = "六点三十七分，林晚又点了一次发布，同一篇文章再次被公众号推送。"
    artifact = _parse(
        text,
        [
            _judgment(after_value="06:37", evidence=[{"excerpt": "六点三十七分", "occurrence": 1}]),
            _judgment(
                change_type="repeated_completed_event",
                after_value="repeated",
                evidence=[{"excerpt": "又点了一次发布", "occurrence": 1}],
            ),
            _judgment(
                change_type="publication_state",
                after_value="published",
                evidence=[{"excerpt": "同一篇文章再次被公众号推送", "occurrence": 1}],
            ),
        ],
    )
    assert [change.change_type for change in artifact.delta.changes] == [
        "clock_state", "repeated_completed_event",
    ]
    assert any(item.reason == "publication_subsumed_by_repeat" for item in artifact.dropped_events)
    validation = validate_delta_v5(artifact.delta)
    outcomes = {item.change_id: item.outcome for item in validation.items}
    by_type = {
        change.change_type: change.change_id
        for change in artifact.delta.changes
    }
    assert outcomes[by_type["clock_state"]] == "valid"
    assert outcomes[by_type["repeated_completed_event"]] == "invalid"
    assert len(validation.accepted_change_ids) == 1


def test_storefront_open_fallback_fires_for_door_opening():
    text = "五点四十九分，阿吴嫌闷，提前拉开临街门透气。"
    artifact = _parse(text, _all_false())
    types = [change.change_type for change in artifact.delta.changes]
    assert "storefront_operation_state" in types
    assert "clock_state" in types
    validation = validate_delta_v5(artifact.delta)
    outcomes = {item.change_id: item.outcome for item in validation.items}
    by_type = {change.change_type: change.change_id for change in artifact.delta.changes}
    assert outcomes[by_type["clock_state"]] == "valid"
    assert outcomes[by_type["storefront_operation_state"]] == "invalid"


def test_storefront_open_fallback_ignores_workshop_door():
    text = "周野推开操作间的门，林晚跟着走了进去。"
    artifact = _parse(text, _all_false(), scene_id="adversarial-storefront-hours")
    assert not any(change.change_type == "storefront_operation_state" for change in artifact.delta.changes)


def test_composite_split_still_works():
    text = "六点四十二分，周野把绿豆汤倒掉，又把碗洗净放进柜子。"
    artifact = _parse(
        text,
        [
            _judgment(
                change_type="object_state",
                after_value="clean_and_stored",
                evidence=[{"excerpt": "周野把绿豆汤倒掉，又把碗洗净放进柜子", "occurrence": 1}],
            )
        ] + [item for item in _all_false() if item["change_type"] != "object_state"],
        state_variant="after_augmented",
    )
    object_changes = [
        (change.after_value, change.mechanism)
        for change in artifact.delta.changes
        if change.change_type == "object_state"
    ]
    assert ("empty", "actor_pours_out") in object_changes
    assert ("clean_and_stored", "explicit_action") in object_changes


def test_prompt_has_two_object_judgment_clarification():
    messages = build_messages(text="林晚没有移动。", state_variant="before")
    prompt = messages[1]["content"]
    assert "TWO object_state judgments" in prompt
    assert "one \"empty\" and one \"clean_and_stored\"" in prompt
