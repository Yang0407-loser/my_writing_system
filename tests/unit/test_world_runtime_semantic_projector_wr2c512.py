import json

from experiments.world_runtime_writer_canary.semantic_extractor_wr2c512 import (
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


def _parse(text, items, sample_id="WR2C512-U-01", state_variant="before", scene_id="adversarial-storefront-hours"):
    return parse_semantic_response(
        text=text,
        response_text=json.dumps({"judgments": items}, ensure_ascii=False),
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
    )


def test_multi_clock_fallback_proposes_every_explicit_time():
    text = "五点四十八分，林晚看了一眼手机上的时间；六点十分，她把第一盘欧包送进烤炉。"
    artifact = _parse(text, _all_false())
    clocks = sorted(change.after_value for change in artifact.delta.changes if change.change_type == "clock_state")
    assert clocks == ["05:48", "06:10"]
    assert artifact.projected_event_count == 2


def test_multi_clock_fallback_fills_missing_time_after_model_judgment():
    text = "五点四十八分，林晚看了一眼手机上的时间；六点十分，她把第一盘欧包送进烤炉。"
    judgments = _all_false()
    judgments = [
        {
            "change_type": "clock_state",
            "occurred": True,
            "after_value": "06:10",
            "mode": "actual",
            "epistemic": "asserted",
            "evidence": [{"excerpt": "六点十分", "occurrence": 1}],
        }
    ] + [item for item in judgments if item["change_type"] != "clock_state"]
    artifact = _parse(text, judgments)
    clocks = sorted(change.after_value for change in artifact.delta.changes if change.change_type == "clock_state")
    assert clocks == ["05:48", "06:10"]


def test_composite_object_judgment_is_split_into_empty_and_clean_and_stored():
    text = "六点四十二分，周野把绿豆汤倒掉，又把碗洗净放进柜子。"
    artifact = _parse(
        text,
        [
            {
                "change_type": "object_state",
                "occurred": True,
                "after_value": "clean_and_stored",
                "mode": "actual",
                "epistemic": "asserted",
                "evidence": [{"excerpt": "周野把绿豆汤倒掉，又把碗洗净放进柜子", "occurrence": 1}],
            }
        ] + _all_false(),
        state_variant="after_augmented",
    )
    object_changes = [
        (change.after_value, change.mechanism)
        for change in artifact.delta.changes
        if change.change_type == "object_state"
    ]
    assert ("empty", "actor_pours_out") in object_changes
    assert ("clean_and_stored", "explicit_action") in object_changes


def test_quoted_time_is_still_skipped():
    text = "林晚把定稿文件传进工作群。老吴随即在群里引用正文那句“六点半打烊”，问她是不是写错。"
    artifact = _parse(text, _all_false(), scene_id="adversarial-unpublished-knowledge")
    assert artifact.delta.changes == ()
