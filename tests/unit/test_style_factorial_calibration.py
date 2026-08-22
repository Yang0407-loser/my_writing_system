from pathlib import Path
from unittest import mock

from app.style_axes import (
    compile_anti_ai_surface,
    compile_pov_disclosure,
)
from experiments.style_factorial_calibration.experiment import (
    ARMS,
    _basic_checks,
    audit,
    build,
    execute,
    load_json,
    report,
)


FAKE_TEXT = (
    "控制室的灯灭了以后，闻栀先听见屋顶滴水。水落在金属柜顶，"
    "每一下都比刚才更近。她把手机电筒压低，照到第一只调光箱的编号。"
    "贺沉已经拔掉接线，抱起箱子往库房走。闻栀跟过去，在门边的登记表上"
    "写下编号。六只箱子分了三趟。最后一只落地时，贺沉检查了接口，没有"
    "说后面的排期。闻栀也没有问。她在群里发出次日停排通知，确认发送后"
    "收起手机。控制室仍旧断电，水声隔着走廊传来。调光箱只是暂时移开，"
    "漏水和供电都没有着落。"
) * 4


def test_axis_contracts_are_sparse_and_separated():
    language = compile_anti_ai_surface()
    pov = compile_pov_disclosure()

    assert len(language.guidance) <= 180
    assert len(pov.guidance) <= 180
    assert "视点人物" not in language.guidance
    assert "句法" not in pov.guidance
    assert "同义词" not in pov.guidance


def test_build_and_audit_freeze_all_four_arms(tmp_path: Path):
    manifest = build(tmp_path)
    assert manifest["sample_count"] == 4
    assert manifest["calibration_only"] is True
    assert manifest["excluded_from_formal_analysis"] is True
    assert {item["arm"] for item in manifest["samples"]} == set(ARMS)
    assert len(
        {item["common_input_hash"] for item in manifest["samples"]}
    ) == 1

    preflight = audit(tmp_path)
    assert preflight["status"] == "ready"
    assert preflight["factor_isolation_pass"] is True
    assert preflight["prompt_token_ratio_pass"] is True
    assert preflight["policy_size_pass"] is True

    ledger = load_json(tmp_path / "attempt-ledger.json")
    assert all(
        item == {
            "request_hash": item["request_hash"],
            "status": "pending",
            "attempt_count": 0,
        }
        for item in ledger["samples"].values()
    )


def test_lexical_forbidden_hit_is_diagnostic_not_machine_hard_failure():
    checks = _basic_checks(
        "他们没有恢复供电，只把调光箱转移到库房并完成编号核对，随后发布停排通知。",
        {
            "required_term_groups_diagnostic": [
                ["调光箱"],
                ["停排"],
                ["编号"],
            ],
            "forbidden_terms_diagnostic": ["恢复供电"],
        },
        "stop",
        "完全不同的输入。",
    )

    assert checks["forbidden_terms_diagnostic_found"] == ["恢复供电"]
    assert checks["forbidden_terms_are_not_semantic_hard_gate"] is True
    assert checks["machine_hard_pass"] is True


def test_execute_is_single_attempt_and_report_is_calibration_only(
    tmp_path: Path,
):
    build(tmp_path)
    audit(tmp_path)
    fake_client = mock.MagicMock()
    fake_client.chat_completion.return_value = FAKE_TEXT

    with (
        mock.patch(
            "experiments.style_factorial_calibration.experiment."
            "get_llm_client",
            return_value=fake_client,
        ),
        mock.patch(
            "experiments.style_factorial_calibration.experiment."
            "settings.LLM_API_KEY",
            "test-key",
        ),
        mock.patch(
            "experiments.style_factorial_calibration.experiment."
            "settings.LLM_BASE_URL",
            "https://api.deepseek.com/v1",
        ),
        mock.patch(
            "experiments.style_factorial_calibration.experiment."
            "settings.LLM_MODEL",
            "deepseek-v4-pro",
        ),
    ):
        first = execute(tmp_path)
        second = execute(tmp_path)

    assert first["newly_attempted"] == 4
    assert second["newly_attempted"] == 0
    assert fake_client.chat_completion.call_count == 4

    ledger = load_json(tmp_path / "attempt-ledger.json")
    assert all(
        item["attempt_count"] == 1
        for item in ledger["samples"].values()
    )
    calibration = report(tmp_path)
    assert calibration["calibration_only"] is True
    assert calibration["excluded_from_formal_analysis"] is True
    assert calibration["human_quality_claim"] is None

