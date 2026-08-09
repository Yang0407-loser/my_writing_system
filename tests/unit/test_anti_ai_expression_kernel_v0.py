import json

from experiments.anti_ai_expression_kernel_v0.builder import build_manifest, build_requests
from experiments.anti_ai_expression_kernel_v0.kernel import render_expression_kernel
from experiments.anti_ai_expression_kernel_v0.metrics import evaluate_expression_signals
from experiments.anti_ai_expression_kernel_v0.runner import _content_checks, run_probe


def test_kernel_has_five_expression_only_rules():
    text = render_expression_kernel()

    assert text.count("\n") >= 6
    for needle in ("每500字最多一个", "不再补一句抽象解释", "段尾停在具体动作", "对称节拍模板"):
        assert needle in text
    for forbidden in ("商业网文", "世界真实性", "地点一致", "Narrative Reality"):
        assert forbidden not in text


def test_two_requests_differ_only_by_expression_kernel():
    requests = build_requests()
    assert len(requests) == 2
    payloads = [json.loads(item["messages"][1]["content"]) for item in requests]

    assert payloads[0]["fixed_content_contract"] == payloads[1]["fixed_content_contract"]
    assert sum("expression_kernel" in payload for payload in payloads) == 1
    assert {item["private_arm"] for item in requests} == {"control", "kernel"}
    assert {item["public_label"] for item in requests} == {"文本A", "文本B"}


def test_manifest_freezes_expression_only_isolation():
    manifest = build_manifest()

    assert manifest["generation_requests"] == 2
    assert manifest["single_scene"] is True
    assert manifest["repeats"] == 1
    assert manifest["commercial_policy_present"] is False
    assert manifest["reality_policy_present"] is False
    assert manifest["production_effect"] is False


def test_metrics_detect_targeted_deepseek_patterns():
    text = (
        "灯光像一条线。烤箱低鸣，暖黄灯光又落下来。"
        "面粉像一场雪，一下，又一下。她忽然觉得，这就是生活。"
    )
    metrics = evaluate_expression_signals(text)

    assert metrics["simile_count"] >= 2
    assert metrics["rhythmic_template_count"] >= 1
    assert metrics["emotion_explanation_count"] >= 1
    assert metrics["uplift_closure_count"] >= 1
    assert metrics["repeated_motif_excess"] >= 1
    assert metrics["automatic_rewrite_recommended"] is False


def test_metrics_do_not_treat_concrete_ending_as_uplift():
    metrics = evaluate_expression_signals("她点了保存，把手机扣在膝盖上。")

    assert metrics["uplift_closure_count"] == 0
    assert metrics["emotion_explanation_count"] == 0


def test_metrics_do_not_count_image_nouns_as_similes():
    metrics = evaluate_expression_signals("相机保存了图像，像素不高，旧肖像放在桌上。")

    assert metrics["simile_count"] == 0


def test_probe_calls_each_arm_once_and_requires_blind_review():
    outputs = iter([
        "周野说别开闪光灯，继续揉面。林晚退出店门，走到夜航船台阶，写下《一个只肯把时间分给面包的人》。她想起客户的改稿意见，写了手掌压住面团的动作，点了保存。",
        "周野说别开闪光灯，手掌压住面团。林晚退到夜航船门口，在台阶写下《一个只肯把时间分给面包的人》。客户的修改意见还留在手机里。她点了保存。",
    ])
    calls = []

    def generate(messages):
        calls.append(messages)
        return next(outputs)

    result = run_probe(generate)

    assert len(calls) == 2
    assert result["generation_calls"] == 2
    assert result["revision_calls"] == 0
    assert result["production_effect"] is False
    assert result["human_blind_review_required"] is True
    assert result["promotion_status"] == "pending_blind_review"


def test_content_checks_treat_negative_publish_as_not_published():
    text = (
        "林晚举起手机。周野说别开闪光灯，随后继续揉面。"
        "她退出面包店，走到夜航船门口的台阶坐下，"
        "写《一个只肯把时间分给面包的人》。"
        "她想起改到第七版的PPT，看着周野用手掌压住面团。"
        "最后保存草稿，没有发布。"
    )

    assert all(_content_checks(text).values())


def test_content_checks_do_not_confuse_camera_raise_with_completed_shot():
    text = (
        "林晚举起相机，周野说别开闪光灯，继续揉面。"
        "她退出面包店，到夜航船的台阶坐下，"
        "写《一个只肯把时间分给面包的人》，想起会议和方案，"
        "记下他的手掌如何推开面团，保存草稿，没点发布。"
    )

    assert _content_checks(text)["no_flash_boundary_before_shot"] is True
    assert _content_checks(text)["saved_not_published"] is True


def test_content_checks_do_not_treat_negated_shutter_as_completed_shot():
    text = (
        "林晚举起手机，还没按下快门，周野就说别开闪光灯。"
        "他继续揉面。她退出面包店，到夜航船台阶坐下，"
        "写《一个只肯把时间分给面包的人》，想起公司的数据报表，"
        "记下他的手掌如何压住面团，保存草稿，并未发布。"
    )

    assert _content_checks(text)["no_flash_boundary_before_shot"] is True


def test_content_checks_detect_actual_publication():
    text = "她保存草稿，随后点了发布。"

    assert _content_checks(text)["saved_not_published"] is False
