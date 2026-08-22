import json

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c3 import (
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
        "evidence": [{"excerpt": "林晚把绿豆汤倒进水池", "occurrence": 1}],
    }
    value.update(updates)
    return value


def _parse(text, items, sample_id="WR2C3-U-01", state_variant="after_augmented", scene_id="adversarial-object-and-repeat"):
    return parse_semantic_response(
        text=text,
        response_text=_judgments(items),
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
    )


def test_prompt_is_checklist_without_canonical_fields_or_expected_validation():
    messages = build_messages(text="林晚没有移动。", state_variant="after_augmented")
    prompt = messages[1]["content"]
    for change_type in (
        "storefront_public_sale", "storefront_public_handoff", "knowledge_state",
        "resignation_acknowledgement", "unsourced_project_fact", "object_state",
        "repeated_completed_event", "employment_state", "publication_state",
        "resignation_delivery", "resignation_personal_record", "clock_state",
        "location_state",
    ):
        assert change_type in prompt
    schema = prompt.split("Return exactly:")[1]
    assert '"subject":' not in schema
    assert '"mechanism":' not in schema
    assert '"actor":' not in schema
    assert '"predicate":' not in schema
    assert '"expected_validation":' not in prompt
    assert "JUDGE EVERY TYPE" in prompt


def test_entity_alias_resolves_a_wu_to_coworker_with_group_file_mechanism():
    text = "林晚把整份文档发进工作群。阿吴随即在群里引用正文那句“天亮了”，问她是不是写错。"
    artifact = _parse(
        text,
        [_judgment(
            change_type="knowledge_state",
            after_value="perceived",
            evidence=[
                {"excerpt": "林晚把整份文档发进工作群", "occurrence": 1},
                {"excerpt": "阿吴随即在群里引用正文那句“天亮了”", "occurrence": 1},
            ],
        )],
        state_variant="before",
        scene_id="adversarial-unpublished-knowledge",
    )
    change = artifact.delta.changes[0]
    assert (change.subject, change.predicate, change.after_value, change.mechanism) == (
        "character:coworker", "article_knowledge", "perceived",
        "group_file_send_and_body_response",
    )


def test_institutional_acknowledgement_gets_local_actor_and_chain_order():
    text = "人事邮件确认辞职当天生效；办完交接，系统把她的状态改为已离职。"
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
                evidence=[{"excerpt": "人事邮件确认辞职当天生效", "occurrence": 1}],
            ),
        ],
        state_variant="after",
        scene_id="adversarial-employment-transition",
        sample_id="WR2C3-U-CHAIN",
    )
    changes = artifact.delta.changes
    assert [change.change_type for change in changes] == [
        "resignation_acknowledgement", "employment_state",
    ]
    assert changes[0].actor == "company:hr-system"
    assert changes[0].mechanism == "institutional_reply"
    assert changes[1].mechanism == "acknowledged_effective_resignation"


def test_object_pour_uses_actor_pours_out_and_noop_location_is_suppressed():
    text = "林晚端起桌上的碗，把剩下的绿豆汤倒进水池，空碗放回原位。"
    artifact = _parse(
        text,
        [_judgment(
            evidence=[{"excerpt": "林晚端起桌上的碗，把剩下的绿豆汤倒进水池", "occurrence": 1}],
        )],
    )
    assert artifact.projected_event_count == 1
    change = artifact.delta.changes[0]
    assert (change.predicate, change.after_value, change.mechanism) == (
        "content_state", "empty", "actor_pours_out",
    )
    assert [item.reason for item in artifact.dropped_events] == []

    noop = _parse(
        text,
        [_judgment(
            after_value="lin-wan-home:coffee-table",
            evidence=[{"excerpt": "空碗放回原位", "occurrence": 1}],
        )],
        sample_id="WR2C3-U-NOOP",
    )
    assert noop.delta.changes == ()
    assert [item.reason for item in noop.dropped_events] == ["no_state_change"]


def test_composite_clean_and_stored_is_invalid_without_actor():
    text = "那扇门整晚都锁着，没人进过厨房；可第二天早上，碗已经洗干净收进了柜子。"
    artifact = _parse(
        text,
        [_judgment(
            after_value="clean_and_stored",
            evidence=[{"excerpt": "碗已经洗干净收进了柜子", "occurrence": 1}],
        )],
        sample_id="WR2C3-U-COMPOSITE",
    )
    change = artifact.delta.changes[0]
    assert (change.predicate, change.after_value, change.mechanism) == (
        "content_state", "clean_and_stored", "missing_actor_or_event",
    )


def test_unsourced_role_is_projected_to_new_character():
    text = "新来的采购主管韩冰查看库存后，让周野换一家面粉供应商。"
    artifact = _parse(
        text,
        [_judgment(
            change_type="unsourced_project_fact",
            after_value="采购主管",
            evidence=[{"excerpt": "新来的采购主管韩冰", "occurrence": 1}],
        )],
        sample_id="WR2C3-U-UNSOURCED",
        scene_id="adversarial-object-and-repeat",
    )
    change = artifact.delta.changes[0]
    assert (change.subject, change.predicate, change.after_value, change.mechanism) == (
        "character:han-bing", "identity_role", "bakery_procurement_supervisor", "text_assertion",
    )


def test_clock_and_location_are_projected_deterministically():
    clock = _parse(
        "第三批面团入炉时，墙上的挂钟指向五点四十分。",
        [_judgment(
            change_type="clock_state",
            after_value="05:40",
            evidence=[{"excerpt": "墙上的挂钟指向五点四十分", "occurrence": 1}],
        )],
        state_variant="before",
        scene_id="adversarial-storefront-hours",
        sample_id="WR2C3-U-CLOCK",
    )
    assert (clock.delta.changes[0].after_value, clock.delta.changes[0].mechanism) == (
        "05:40", "explicit_time_progression",
    )

    location = _parse(
        "周野推开操作间的门，林晚跟着走进来，在案板前站定。",
        [_judgment(
            change_type="location_state",
            after_value="操作间",
            evidence=[
                {"excerpt": "林晚跟着走进来", "occurrence": 1},
                {"excerpt": "周野推开操作间的门", "occurrence": 1},
            ],
        )],
        state_variant="before",
        scene_id="adversarial-storefront-hours",
        sample_id="WR2C3-U-LOC",
    )
    change = location.delta.changes[0]
    assert (change.subject, change.after_value, change.mechanism) == (
        "character:lin-wan", "bakery:wild-bread:workshop", "explicit_entry",
    )


def test_non_actual_and_negated_judgments_are_dropped():
    text = "顾客打开付款码，周野摆手没收钱，面包也留在柜台上。"
    artifact = _parse(
        text,
        [
            _judgment(change_type="storefront_public_sale", occurred=False),
            _judgment(change_type="storefront_public_handoff", occurred=False),
            _judgment(change_type="storefront_public_sale", occurred=True, mode="planned"),
        ],
        state_variant="before",
        scene_id="adversarial-storefront-hours",
        sample_id="WR2C3-U-NEG",
    )
    assert artifact.delta.changes == ()
    reasons = [item.reason for item in artifact.dropped_events]
    assert reasons.count("judged_not_occurred") == 2
    assert any(reason.startswith("non_actual") for reason in reasons)
