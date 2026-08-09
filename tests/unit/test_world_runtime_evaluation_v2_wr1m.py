import json

from experiments.world_runtime_writer_canary.evaluation_v2 import (
    build_blind_review_package,
    evaluate_runtime,
    evaluate_text,
)


def _evaluate(text: str):
    return evaluate_text(
        sample_id="fixture-1",
        scene_id="fixture-scene",
        arm="A",
        repeat=1,
        text=text,
    )


def _event(result, event_id):
    return next(item for item in result.events if item.event_id == event_id)


def test_publication_outcome_is_separate_from_required_submit_bridge():
    result = _evaluate("林晚按下发布键，页面显示已发布。")
    event = _event(result, "event:publish-article")

    assert event.required_outcome.outcome == "pass"
    assert event.required_bridge.outcome == "fail"
    assert event.illegal_transition.outcome == "fail"
    assert event.strict_pass is False


def test_explicit_publication_path_preserves_evidence_spans():
    text = "林晚点下提交，页面先显示已提交，审核通过后变成已发布。"
    result = _evaluate(text)
    event = _event(result, "event:publish-article")

    assert event.strict_pass is True
    assert event.required_bridge.evidence
    for evidence in (
        *event.required_outcome.evidence,
        *event.required_bridge.evidence,
    ):
        assert text[evidence.start:evidence.end] == evidence.excerpt


def test_recipient_name_and_link_do_not_prove_article_was_read():
    text = "文章已发布。林晚复制链接发给季晴。季晴回了句：你写的？"
    result = _evaluate(text)
    event = _event(result, "event:share-with-jiqing")

    assert event.required_bridge.outcome == "pass"
    assert event.required_outcome.outcome == "unresolved"
    assert event.evidence_sufficiency.outcome == "unresolved"
    assert event.strict_pass is False


def test_explicit_read_completes_available_reached_perceived_path():
    text = "文章已发布。林晚复制链接发给季晴。季晴点开后说：看完了。"
    result = _evaluate(text)
    event = _event(result, "event:share-with-jiqing")

    assert event.required_outcome.outcome == "pass"
    assert event.required_bridge.outcome == "pass"
    assert event.evidence_sufficiency.outcome == "pass"
    assert event.strict_pass is True


def test_entry_accepts_door_then_verb_and_actor_permission_wording():
    text = "周野站在门内，把门拉开一道窄缝，侧身让她进来。操作间里有麦香。"
    result = _evaluate(text)
    event = _event(result, "event:enter-workshop")

    assert event.required_outcome.outcome == "pass"
    assert event.required_bridge.outcome == "pass"
    assert event.strict_pass is True


def test_resignation_delivery_uses_send_after_hr_channel_not_prior_chat_send():
    text = (
        "她先把链接点了发送。随后打开公司人事部邮箱，写好辞职通知，"
        "按下发送，页面显示邮件已发送。"
    )
    result = _evaluate(text)
    event = _event(result, "event:deliver-resignation")

    assert event.required_outcome.outcome == "pass"
    assert event.required_outcome.evidence[-1].start > event.required_outcome.evidence[0].start
    assert event.strict_pass is True


def test_unsourced_setting_candidates_are_typed_not_auto_rejected():
    text = (
        "季晴是她的编辑。文章写的是连锁面包店如何使用过期原料。"
        "辞职信里填了最后工作日。"
    )
    result = _evaluate(text)
    candidates = result.setting_candidates

    assert {item.category for item in candidates} == {
        "new_relationship",
        "new_project_fact",
        "state_change",
    }
    assert all(item.review_required for item in candidates)
    assert all(
        text[item.evidence.start:item.evidence.end] == item.evidence.excerpt
        for item in candidates
    )


def test_existing_runtime_replay_is_posthoc_and_does_not_promote():
    result = evaluate_runtime()

    assert result["evaluation_role"] == "posthoc_diagnostic_not_promotion_evidence"
    assert result["source_outputs"] == 8
    assert result["promotion_eligible"] is False
    assert result["aggregate"]["B"]["bridge_passes"] > result["aggregate"]["A"]["bridge_passes"]
    assert "contract_authored_after_outputs_were_available" in result["limitations"]


def test_blind_review_package_hides_arms_and_preserves_private_key():
    receipt = build_blind_review_package()
    root = __import__(
        "experiments.world_runtime_writer_canary.evaluation_v2",
        fromlist=["DEFAULT_RUNTIME"],
    ).DEFAULT_RUNTIME
    package = json.loads(
        (root / "private/human-review-v2.json").read_text(encoding="utf-8")
    )
    key = json.loads(
        (root / "private/human-review-v2-key.json").read_text(encoding="utf-8")
    )

    assert receipt["candidate_count"] == 8
    assert package["arms_hidden"] is True
    assert len(package["candidates"]) == 8
    assert all("arm" not in item and "sample_id" not in item for item in package["candidates"])
    assert all(item["scene_context"]["premise"] for item in package["candidates"])
    assert len(package["review_contract"]["required_events"]) == 4
    assert set(package["review_contract"]["scales"]) == set(
        package["candidates"][0]["review_fields"]
    ) - {"notes"}
    assert set(key) == {item["candidate_id"] for item in package["candidates"]}
