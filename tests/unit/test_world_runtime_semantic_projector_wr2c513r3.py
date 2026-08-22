import json

from experiments.world_runtime_writer_canary.semantic_extractor_wr2c513r3 import (
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
        "change_type": "object_state",
        "occurred": True,
        "after_value": "clean_and_stored",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "周野把碗洗净放进柜子", "occurrence": 1}],
    }
    value.update(updates)
    return value


def _parse(text, items, sample_id="WR2C513R3-U-01", state_variant="after_augmented", scene_id="adversarial-object-and-repeat"):
    return parse_semantic_response(
        text=text,
        response_text=json.dumps({"judgments": items}, ensure_ascii=False),
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
    )


def test_clean_and_stored_with_unique_full_text_actor_is_explicit_action():
    text = "周野把碗洗净放进柜子。"
    artifact = _parse(text, [_judgment()] + [item for item in _all_false() if item["change_type"] != "object_state"])
    change = next(
        c for c in artifact.delta.changes
        if c.change_type == "object_state" and c.after_value == "clean_and_stored"
    )
    assert change.mechanism == "explicit_action"


def test_passive_composite_without_actor_stays_missing_actor():
    text = "门窗整夜锁着，没人进屋；早上发现碗已经洗干净收进柜子。"
    artifact = _parse(
        text,
        [_judgment(evidence=[{"excerpt": "碗已经洗干净收进柜子", "occurrence": 1}])]
        + [item for item in _all_false() if item["change_type"] != "object_state"],
    )
    change = next(
        c for c in artifact.delta.changes
        if c.change_type == "object_state" and c.after_value == "clean_and_stored"
    )
    assert change.mechanism == "missing_actor_or_event"


def test_prompt_counts_impossible_knowledge_as_occurred():
    messages = build_messages(text="林晚没有移动。", state_variant="before")
    prompt = messages[1]["content"]
    assert "appears impossible" in prompt
    assert "STILL occurred=true" in prompt


def test_multi_clock_still_works():
    text = "五点四十八分，林晚看了一眼手机上的时间；六点十分，她把第一盘欧包送进烤炉。"
    artifact = _parse(
        text,
        [item for item in _all_false()],
        state_variant="before",
        scene_id="adversarial-storefront-hours",
    )
    clocks = sorted(
        change.after_value for change in artifact.delta.changes
        if change.change_type == "clock_state"
    )
    assert clocks == ["05:48", "06:10"]
