import json

from experiments.anti_ai_expression_kernel_v0.kernel import expression_kernel_hash
from experiments.anti_ai_expression_kernel_v0_replication.builder import (
    build_manifest,
    build_requests,
)
from experiments.anti_ai_expression_kernel_v0_replication.runner import (
    _content_checks,
    render_blind_review,
    run_replication,
)


def test_replication_has_two_scenes_and_four_calls():
    manifest = build_manifest()

    assert manifest["scene_count"] == 2
    assert manifest["generation_requests"] == 4
    assert manifest["repeats_per_arm_per_scene"] == 1
    assert manifest["kernel_hash"] == expression_kernel_hash()
    assert manifest["commercial_policy_present"] is False
    assert manifest["reality_policy_present"] is False
    assert manifest["production_effect"] is False


def test_each_scene_pair_differs_only_by_frozen_kernel():
    requests = build_requests()
    for scene_id in {request["scene_id"] for request in requests}:
        pair = [request for request in requests if request["scene_id"] == scene_id]
        assert len(pair) == 2
        payloads = [json.loads(request["messages"][1]["content"]) for request in pair]
        assert payloads[0]["fixed_content_contract"] == payloads[1]["fixed_content_contract"]
        assert sum("expression_kernel" in payload for payload in payloads) == 1
        assert {request["private_arm"] for request in pair} == {"control", "kernel"}


def test_handover_content_checks_accept_complete_paraphrase():
    text = (
        "陈默把交接材料的文件夹推过去。唐主管问：考虑清楚了？"
        "她说考虑清楚了。唐主管在末页签字。"
        "她把门禁卡放在文件夹上，走进电梯。电梯门合上。"
    )

    assert all(_content_checks("handover", text).values())


def test_bicycle_content_checks_accept_complete_paraphrase():
    text = (
        "订单还剩十二分钟。贺舟把自行车翻过来查看链条。"
        "路人问要不要帮忙。他说不用修，拿手机照一下齿盘。"
        "他解开后轮边的链条，重新套上齿盘，转动脚踏确认。"
        "他说谢谢，扶正车骑上去，继续赶订单。"
    )

    assert all(_content_checks("bicycle_chain", text).values())


def test_replication_calls_exactly_four_times_without_revision():
    handover = (
        "陈默推过文件夹。唐主管问考虑清楚了？她说清楚。主管签字。"
        "她把门禁卡放下，走进电梯，电梯门合上。"
    )
    bicycle = (
        "倒计时还剩十分钟。贺舟把车翻过来检查链条。路人问要不要帮忙。"
        "他说不用修，只要手机照亮齿盘。他把链条套上齿盘，转动脚踏，"
        "说了谢谢，骑上车继续赶订单。"
    )
    outputs = iter([handover, handover, bicycle, bicycle])
    calls = []

    def generate(messages):
        calls.append(messages)
        return next(outputs)

    result = run_replication(generate)

    assert len(calls) == 4
    assert result["generation_calls"] == 4
    assert result["revision_calls"] == 0
    assert result["kernel_frozen"] is True
    assert result["production_effect"] is False
    assert result["human_blind_review_required"] is True


def test_blind_markdown_does_not_reveal_private_arms():
    outputs = iter(["甲", "乙", "丙", "丁"])
    result = run_replication(lambda _messages: next(outputs))

    blind = render_blind_review(result)

    assert "private_arm" not in blind
    assert "control" not in blind.lower()
    assert '"private_arm": "kernel"' not in blind.lower()
    assert "场景1·文本A" not in blind
    assert "## 文本A" in blind
