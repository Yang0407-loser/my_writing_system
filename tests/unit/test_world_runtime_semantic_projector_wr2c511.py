import json

from experiments.world_runtime_writer_canary.delta_shadow_wr2c5 import validate_delta_v5
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c511 import (
    build_messages,
    parse_semantic_response,
)


def _judgments(items):
    return json.dumps({"judgments": items}, ensure_ascii=False)


def _all_false():
    types = (
        "storefront_public_sale", "storefront_public_handoff", "storefront_operation_state",
        "knowledge_state", "resignation_acknowledgement", "unsourced_project_fact",
        "object_state", "repeated_completed_event", "employment_state", "publication_state",
        "resignation_delivery", "resignation_personal_record", "clock_state", "location_state",
    )
    return [
        {
            "change_type": change_type,
            "occurred": False,
            "after_value": None,
            "mode": "actual",
            "epistemic": "asserted",
            "evidence": [],
        }
        for change_type in types
    ]


def _parse(text, items, sample_id="WR2C511-U-01", state_variant="before", scene_id="adversarial-storefront-hours"):
    return parse_semantic_response(
        text=text,
        response_text=_judgments(items),
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
    )


def test_deterministic_clock_fallback_fires_when_model_misses():
    text = "五点四十八分，林晚看了一眼手机上的时间，继续整理文章。"
    artifact = _parse(text, _all_false())
    assert artifact.projected_event_count == 1
    change = artifact.delta.changes[0]
    assert (change.change_type, change.after_value, change.mechanism) == (
        "clock_state", "05:48", "explicit_time_progression",
    )
    evidence = artifact.delta.evidence[0]
    assert text[evidence.start:evidence.end] == "五点四十八分"


def test_clock_fallback_skips_quoted_time():
    text = "林晚把定稿文件传进工作群。老吴随即在群里引用正文那句“六点半打烊”，问她是不是写错。"
    artifact = _parse(text, _all_false(), state_variant="before", scene_id="adversarial-unpublished-knowledge")
    assert artifact.delta.changes == ()


def test_clock_fallback_does_not_override_model_judgment():
    text = "五点四十八分，林晚看了一眼手机上的时间，继续整理文章。"
    artifact = _parse(text, [{
        "change_type": "clock_state",
        "occurred": True,
        "after_value": "05:48",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "五点四十八分", "occurrence": 1}],
    }])
    assert artifact.projected_event_count == 1
    assert artifact.delta.changes[0].after_value == "05:48"


def test_knowledge_perceiver_selected_by_distance_in_merged_excerpt():
    text = "林晚把定稿文件传进工作群。老吴随即在群里引用正文那句“六点半打烊”，问她是不是写错。"
    artifact = _parse(
        text,
        [{
            "change_type": "knowledge_state",
            "occurred": True,
            "after_value": "perceived",
            "mode": "actual",
            "epistemic": "asserted",
            "evidence": [{
                "excerpt": "林晚把定稿文件传进工作群。老吴随即在群里引用正文那句“六点半打烊”",
                "occurrence": 1,
            }],
        }],
        scene_id="adversarial-unpublished-knowledge",
    )
    change = artifact.delta.changes[0]
    assert change.subject == "character:coworker"
    assert change.mechanism == "group_file_send_and_body_response"


def test_dependency_ordering_and_sale_dedup_regression():
    text = "六点十四分，门已打开，顾客扫码付款，周野把吐司递给对方。"
    artifact = _parse(
        text,
        [
            {
                "change_type": "storefront_public_sale",
                "occurred": True,
                "after_value": "occurred",
                "mode": "actual",
                "epistemic": "asserted",
                "evidence": [{"excerpt": "顾客扫码付款", "occurrence": 1}],
            },
            {
                "change_type": "storefront_operation_state",
                "occurred": True,
                "after_value": "open",
                "mode": "actual",
                "epistemic": "asserted",
                "evidence": [{"excerpt": "门已打开", "occurrence": 1}],
            },
            {
                "change_type": "clock_state",
                "occurred": True,
                "after_value": "06:14",
                "mode": "actual",
                "epistemic": "asserted",
                "evidence": [{"excerpt": "六点十四分", "occurrence": 1}],
            },
            {
                "change_type": "storefront_public_handoff",
                "occurred": True,
                "after_value": "occurred",
                "mode": "actual",
                "epistemic": "asserted",
                "evidence": [{"excerpt": "周野把吐司递给对方", "occurrence": 1}],
            },
        ],
    )
    assert [change.change_type for change in artifact.delta.changes] == [
        "clock_state", "storefront_operation_state", "storefront_public_sale",
    ]
    validation = validate_delta_v5(artifact.delta)
    assert len(validation.accepted_change_ids) == 3
    assert len(validation.rejected_change_ids) == 0


def test_prompt_unchanged_has_fourteen_types():
    messages = build_messages(text="林晚没有移动。", state_variant="before")
    prompt = messages[1]["content"]
    assert "14 judgments" in prompt
    assert "storefront_operation_state" in prompt
