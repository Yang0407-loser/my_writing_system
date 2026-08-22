import json

from experiments.world_runtime_writer_canary.semantic_extractor_wr2c4 import (
    build_messages,
    parse_semantic_response,
)


def _judgments(items):
    return json.dumps({"judgments": items}, ensure_ascii=False)


def _judgment(**updates):
    value = {
        "change_type": "object_state",
        "occurred": True,
        "after_value": "empty",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "将剩下的绿豆汤倒掉", "occurrence": 1}],
    }
    value.update(updates)
    return value


def _parse(text, items, sample_id="WR2C4-U-01", state_variant="after_augmented", scene_id="adversarial-object-and-repeat"):
    return parse_semantic_response(
        text=text,
        response_text=_judgments(items),
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
    )


def test_prompt_forbids_composite_values_and_counts_self_declared_termination():
    messages = build_messages(text="林晚没有移动。", state_variant="after_augmented")
    prompt = messages[1]["content"]
    assert "empty_and_restored" in prompt
    assert "single state word" in prompt
    assert "自己认为" in prompt
    assert "occurred=true" in prompt
    schema = prompt.split("Return exactly:")[1]
    assert '"subject":' not in schema
    assert '"mechanism":' not in schema


def test_composite_empty_and_restored_normalizes_with_unique_full_text_actor():
    text = "林晚把碗端到水池边，将剩下的绿豆汤倒掉，又把空碗搁回原处。"
    artifact = _parse(
        text,
        [_judgment(after_value="empty_and_restored")],
        sample_id="WR2C4-U-COMPOSITE",
    )
    assert artifact.projected_event_count == 1
    change = artifact.delta.changes[0]
    assert (change.predicate, change.after_value, change.mechanism, change.actor) == (
        "content_state", "empty", "actor_pours_out", "character:lin-wan",
    )


def test_composite_clean_and_stored_without_any_character_stays_missing_actor():
    text = "门窗一夜没开，没人进屋；早上发现碗已经洗净收进橱柜。"
    artifact = _parse(
        text,
        [_judgment(
            after_value="clean_and_stored",
            evidence=[{"excerpt": "碗已经洗净收进橱柜", "occurrence": 1}],
        )],
        sample_id="WR2C4-U-COMPOSITE2",
    )
    change = artifact.delta.changes[0]
    assert (change.predicate, change.after_value, change.mechanism) == (
        "content_state", "clean_and_stored", "missing_actor_or_event",
    )


def test_multiple_characters_without_evidence_actor_stays_conservative():
    text = "林晚和周野都在店里；碗里的绿豆汤被倒进了水池。"
    artifact = _parse(
        text,
        [_judgment(evidence=[{"excerpt": "碗里的绿豆汤被倒进了水池", "occurrence": 1}])],
        sample_id="WR2C4-U-MULTI",
    )
    assert artifact.projected_event_count == 1
    change = artifact.delta.changes[0]
    assert change.mechanism == "missing_actor_or_event"


def test_self_declared_employment_end_projects_to_self_assumed_mechanism():
    text = "邮箱没有回音，林晚单方面宣布劳动关系已经结束，说下周一不再来。"
    artifact = _parse(
        text,
        [_judgment(
            change_type="employment_state",
            after_value="ended",
            evidence=[{"excerpt": "林晚单方面宣布劳动关系已经结束", "occurrence": 1}],
        )],
        state_variant="after",
        scene_id="adversarial-employment-transition",
        sample_id="WR2C4-U-SELF",
    )
    change = artifact.delta.changes[0]
    assert (change.predicate, change.after_value, change.mechanism) == (
        "status", "ended", "self_assumed_effective",
    )


def test_chain_ordering_keeps_ack_before_employment():
    text = "人事系统发来确认函，写明辞职当日生效；交接完成后，系统把她的状态改为已离职。"
    artifact = _parse(
        text,
        [
            _judgment(
                change_type="employment_state",
                after_value="ended",
                evidence=[{"excerpt": "系统把她的状态改为已离职", "occurrence": 1}],
            ),
            _judgment(
                change_type="resignation_acknowledgement",
                after_value=True,
                evidence=[{"excerpt": "人事系统发来确认函，写明辞职当日生效", "occurrence": 1}],
            ),
        ],
        state_variant="after",
        scene_id="adversarial-employment-transition",
        sample_id="WR2C4-U-CHAIN",
    )
    changes = artifact.delta.changes
    assert [change.change_type for change in changes] == [
        "resignation_acknowledgement", "employment_state",
    ]
    assert changes[0].actor == "company:hr-system"
