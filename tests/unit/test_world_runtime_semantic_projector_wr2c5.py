import json

from experiments.world_runtime_writer_canary.delta_shadow_wr2c5 import validate_delta_v5
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c5 import (
    build_messages,
    parse_semantic_response,
)


def _judgments(items):
    return json.dumps({"judgments": items}, ensure_ascii=False)


def _judgment(**updates):
    value = {
        "change_type": "clock_state",
        "occurred": True,
        "after_value": "05:31",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "五点三十一分", "occurrence": 1}],
    }
    value.update(updates)
    return value


def _parse(text, items, sample_id="WR2C5-U-01", state_variant="before", scene_id="adversarial-storefront-hours"):
    return parse_semantic_response(
        text=text,
        response_text=_judgments(items),
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
    )


def test_prompt_has_fourteen_types_narrative_clock_and_operation():
    messages = build_messages(text="林晚没有移动。", state_variant="before")
    prompt = messages[1]["content"]
    assert "14 judgments" in prompt
    assert "storefront_operation_state" in prompt
    assert "六点钟" in prompt
    assert "五点三十一分" in prompt
    schema = prompt.split("Return exactly:")[1]
    assert '"subject":' not in schema
    assert '"mechanism":' not in schema


def test_narrative_clock_expression_is_projected():
    artifact = _parse(
        "五点三十一分，林晚看了一眼屏幕右下角的时间，把光标移到发布设置上。",
        [_judgment()],
    )
    change = artifact.delta.changes[0]
    assert (change.change_type, change.after_value, change.mechanism) == (
        "clock_state", "05:31", "explicit_time_progression",
    )


def test_open_after_clock_makes_sale_valid_end_to_end():
    text = "六点十分，林晚刚把卷帘门拉开，顾客就扫码付了款，周野把面包递出去。"
    artifact = _parse(
        text,
        [
            _judgment(after_value="06:10", evidence=[{"excerpt": "六点十分", "occurrence": 1}]),
            _judgment(
                change_type="storefront_operation_state",
                after_value="open",
                evidence=[{"excerpt": "把卷帘门拉开", "occurrence": 1}],
            ),
            _judgment(
                change_type="storefront_public_sale",
                after_value="occurred",
                evidence=[{"excerpt": "顾客就扫码付了款", "occurrence": 1}],
            ),
        ],
    )
    validation = validate_delta_v5(artifact.delta)
    assert set(validation.accepted_change_ids) == {
        change.change_id for change in artifact.delta.changes
    }
    assert len(validation.rejected_change_ids) == 0


def test_early_open_is_invalid():
    text = "五点，林晚提前拉开卷帘门开始营业。"
    artifact = _parse(
        text,
        [
            _judgment(after_value="05:00", evidence=[{"excerpt": "五点", "occurrence": 1}]),
            _judgment(
                change_type="storefront_operation_state",
                after_value="open",
                evidence=[{"excerpt": "提前拉开卷帘门开始营业", "occurrence": 1}],
            ),
        ],
    )
    validation = validate_delta_v5(artifact.delta)
    outcomes = {item.change_id: item.outcome for item in validation.items}
    changes = {change.change_id: change for change in artifact.delta.changes}
    clock_id = next(cid for cid, c in changes.items() if c.change_type == "clock_state")
    open_id = next(cid for cid, c in changes.items() if c.change_type == "storefront_operation_state")
    assert outcomes[clock_id] == "valid"
    assert outcomes[open_id] == "invalid"


def test_sale_before_open_without_open_chain_is_invalid():
    artifact = _parse(
        "五点五十分，顾客扫码付款，周野从窗口递出面包。",
        [_judgment(
            change_type="storefront_public_sale",
            after_value="occurred",
            evidence=[{"excerpt": "顾客扫码付款", "occurrence": 1}],
        )],
    )
    validation = validate_delta_v5(artifact.delta)
    assert len(validation.accepted_change_ids) == 0
    assert len(validation.rejected_change_ids) == 1


def test_storefront_open_projection_is_canonical():
    artifact = _parse(
        "六点整，林晚走到门口，把卷帘门往上推，店铺开始营业。",
        [
            _judgment(after_value="06:00", evidence=[{"excerpt": "六点整", "occurrence": 1}]),
            _judgment(
                change_type="storefront_operation_state",
                after_value="open",
                evidence=[{"excerpt": "把卷帘门往上推，店铺开始营业", "occurrence": 1}],
            ),
        ],
    )
    change = next(
        c for c in artifact.delta.changes if c.change_type == "storefront_operation_state"
    )
    assert (change.subject, change.predicate, change.after_value, change.mechanism) == (
        "bakery:wild-bread:storefront", "operation_state", "open", "explicit_open_close",
    )
