import json

from experiments.world_runtime_writer_canary.delta_shadow_wr2c5 import validate_delta_v5
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c51 import (
    build_messages,
    parse_semantic_response,
)


def _judgments(items):
    return json.dumps({"judgments": items}, ensure_ascii=False)


def _judgment(**updates):
    value = {
        "change_type": "clock_state",
        "occurred": True,
        "after_value": "05:43",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "五点四十三分", "occurrence": 1}],
    }
    value.update(updates)
    return value


def _parse(text, items, sample_id="WR2C51-U-01", state_variant="before", scene_id="adversarial-storefront-hours"):
    return parse_semantic_response(
        text=text,
        response_text=_judgments(items),
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
    )


def test_prompt_clarifies_handoff_and_unsourced():
    messages = build_messages(text="林晚没有移动。", state_variant="before")
    prompt = messages[1]["content"]
    assert "NOT a separate handoff" in prompt
    assert "新来的收银员孙岚" in prompt
    assert "draft/fiction-within-fiction are NOT durable" in prompt
    assert "14 judgments" in prompt


def test_narrative_clock_is_projected():
    artifact = _parse(
        "五点四十三分，林晚看了一眼电脑右下角的时间，继续修改文章。",
        [_judgment()],
    )
    change = artifact.delta.changes[0]
    assert (change.change_type, change.after_value, change.mechanism) == (
        "clock_state", "05:43", "explicit_time_progression",
    )


def test_dependency_ordering_fixes_sale_after_open_even_if_model_outputs_sale_first():
    text = "六点十二分，卷帘门已经拉起，顾客扫码付款，周野把牛角包递给对方。"
    artifact = _parse(
        text,
        [
            _judgment(
                change_type="storefront_public_sale",
                after_value="occurred",
                evidence=[{"excerpt": "顾客扫码付款", "occurrence": 1}],
            ),
            _judgment(
                change_type="storefront_operation_state",
                after_value="open",
                evidence=[{"excerpt": "卷帘门已经拉起", "occurrence": 1}],
            ),
            _judgment(after_value="06:12", evidence=[{"excerpt": "六点十二分", "occurrence": 1}]),
        ],
    )
    types = [change.change_type for change in artifact.delta.changes]
    assert types == ["clock_state", "storefront_operation_state", "storefront_public_sale"]
    validation = validate_delta_v5(artifact.delta)
    assert len(validation.accepted_change_ids) == 3
    assert len(validation.rejected_change_ids) == 0


def test_handoff_inside_paid_sale_is_dropped():
    text = "六点十二分，卷帘门已经拉起，顾客扫码付款，周野把牛角包递给对方。"
    artifact = _parse(
        text,
        [
            _judgment(after_value="06:12", evidence=[{"excerpt": "六点十二分", "occurrence": 1}]),
            _judgment(
                change_type="storefront_operation_state",
                after_value="open",
                evidence=[{"excerpt": "卷帘门已经拉起", "occurrence": 1}],
            ),
            _judgment(
                change_type="storefront_public_sale",
                after_value="occurred",
                evidence=[{"excerpt": "顾客扫码付款", "occurrence": 1}],
            ),
            _judgment(
                change_type="storefront_public_handoff",
                after_value="occurred",
                evidence=[{"excerpt": "周野把牛角包递给对方", "occurrence": 1}],
            ),
        ],
    )
    assert [change.change_type for change in artifact.delta.changes] == [
        "clock_state", "storefront_operation_state", "storefront_public_sale",
    ]
    assert any(item.reason == "handoff_subsumed_by_sale" for item in artifact.dropped_events)
    validation = validate_delta_v5(artifact.delta)
    assert len(validation.accepted_change_ids) == 3


def test_free_handoff_without_sale_is_kept():
    text = "六点二十分，店门开着，周野把一只牛角包免费塞给路过的孩子。"
    artifact = _parse(
        text,
        [
            _judgment(after_value="06:20", evidence=[{"excerpt": "六点二十分", "occurrence": 1}]),
            _judgment(
                change_type="storefront_operation_state",
                after_value="open",
                evidence=[{"excerpt": "店门开着", "occurrence": 1}],
            ),
            _judgment(
                change_type="storefront_public_handoff",
                after_value="occurred",
                evidence=[{"excerpt": "把一只牛角包免费塞给路过的孩子", "occurrence": 1}],
            ),
        ],
    )
    assert [change.change_type for change in artifact.delta.changes] == [
        "clock_state", "storefront_operation_state", "storefront_public_handoff",
    ]
    validation = validate_delta_v5(artifact.delta)
    assert len(validation.accepted_change_ids) == 3


def test_early_open_invalid_and_preopen_sale_with_clock():
    early = _parse(
        "四点五十分，林晚提前开门营业。",
        [
            _judgment(after_value="04:50", evidence=[{"excerpt": "四点五十分", "occurrence": 1}]),
            _judgment(
                change_type="storefront_operation_state",
                after_value="open",
                evidence=[{"excerpt": "提前开门营业", "occurrence": 1}],
            ),
        ],
    )
    validation = validate_delta_v5(early.delta)
    outcomes = {item.change_id: item.outcome for item in validation.items}
    assert outcomes[early.delta.changes[0].change_id] == "valid"
    assert outcomes[early.delta.changes[1].change_id] == "invalid"

    preopen = _parse(
        "五点五十五分，顾客扫码付款，周野从窗口递出面包。",
        [
            _judgment(after_value="05:55", evidence=[{"excerpt": "五点五十五分", "occurrence": 1}]),
            _judgment(
                change_type="storefront_public_sale",
                after_value="occurred",
                evidence=[{"excerpt": "顾客扫码付款", "occurrence": 1}],
            ),
        ],
    )
    validation = validate_delta_v5(preopen.delta)
    outcomes = {item.change_id: item.outcome for item in validation.items}
    assert outcomes[preopen.delta.changes[0].change_id] == "valid"
    assert outcomes[preopen.delta.changes[1].change_id] == "invalid"
