import json

from experiments.world_runtime_writer_canary.semantic_extractor_wr2c513r9 import (
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


def test_prompt_contains_impossible_perception_worked_example():
    messages = build_messages(text="正文。", state_variant="before")
    prompt = messages[1]["content"]
    assert "WORKED EXAMPLE" in prompt
    assert "能背出" in prompt
    assert "由 Validator 决定" in prompt
    assert "WORKED EXAMPLE 2" in prompt
    assert "打开刚上传的文件并念出" in prompt
    assert "WORKED EXAMPLE 3" in prompt
    assert "受理确认" in prompt and "delivery" in prompt
    assert "WORKED EXAMPLE 4" in prompt
    assert "NEVER join excerpts across a paragraph break" in prompt


def test_impossible_perception_judgment_projects_to_invalid_knowledge():
    text = "老吴能逐字背出那篇终稿的结尾，可稿子从未离开过林晚的加密草稿箱，也没有发给过任何人。"
    judgments = _all_false()
    judgments[_TYPES.index("knowledge_state")] = {
        "change_type": "knowledge_state",
        "occurred": True,
        "after_value": "perceived",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "老吴能逐字背出那篇终稿的结尾", "occurrence": 1}],
    }
    artifact = parse_semantic_response(
        text=text,
        response_text=json.dumps({"judgments": judgments}, ensure_ascii=False),
        sample_id="EXTRACTOR-R9-U-01",
        scene_id="extractor-r7",
        state_variant="before",
        base_revision=7,
    )
    change = next(c for c in artifact.delta.changes if c.change_type == "knowledge_state")
    assert change.after_value == "perceived"
    assert change.mechanism == "missing_transmission_path"
    assert change.subject == "character:coworker"
