import json

from experiments.world_runtime_writer_canary.semantic_extractor_wr2c513r4 import (
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


def _parse(text, items, sample_id="WR2C513R4-U-01"):
    return parse_semantic_response(
        text=text,
        response_text=json.dumps({"judgments": items}, ensure_ascii=False),
        sample_id=sample_id,
        scene_id="adversarial-storefront-hours",
        state_variant="before",
        base_revision=7,
    )


def test_past_day_clock_reference_is_not_projected_as_scene_time():
    text = "面种是昨晚十点喂过的；四点五十分，烤箱预热完成。"
    artifact = _parse(text, _all_false())
    clocks = [
        change.after_value
        for change in artifact.delta.changes
        if change.change_type == "clock_state"
    ]
    assert clocks == ["04:50"]


def test_shifted_marker_in_judgment_evidence_is_dropped():
    text = "面种是昨晚十点喂过的。"
    judgments = _all_false()
    judgments[_TYPES.index("clock_state")] = {
        "change_type": "clock_state",
        "occurred": True,
        "after_value": "10:00",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "昨晚十点", "occurrence": 1}],
    }
    artifact = _parse(text, judgments)
    assert not [c for c in artifact.delta.changes if c.change_type == "clock_state"]


def test_future_day_reference_is_not_projected_as_scene_time():
    text = "明天六点开门营业。"
    artifact = _parse(text, _all_false())
    assert not [c for c in artifact.delta.changes if c.change_type == "clock_state"]


def test_multi_clock_scene_times_still_work():
    text = "五点四十八分，林晚看了一眼手机上的时间；六点十分，她把第一盘贝果送进烤炉。"
    artifact = _parse(text, _all_false())
    clocks = sorted(
        change.after_value
        for change in artifact.delta.changes
        if change.change_type == "clock_state"
    )
    assert clocks == ["05:48", "06:10"]
