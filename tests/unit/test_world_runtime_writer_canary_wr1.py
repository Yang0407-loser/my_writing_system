import json

import pytest

from experiments.world_runtime_writer_canary import experiment


def _good_text() -> str:
    core = (
        "四点二十分，临街卷帘门仍压在地面。周野从操作间打开侧门，让林晚进去。"
        "她在工作台旁打开电脑，先点下提交，等审核通过，页面显示文章已经发布。"
        "她复制文章链接发给季晴。季晴点开链接读完，回了一句收到。"
        "林晚随后检查辞职通知的附件与收件地址，把邮件发送到公司人事邮箱。"
    )
    return core + "烤箱的热气贴着地面缓慢散开，她没有解释，只确认每一步留下的回执。" * 12


def _bad_text() -> str:
    core = (
        "四点二十分，周野让店面开门营业，顾客走进店里。公司内网已经有同事评论。"
        "他又从操作间打开侧门让林晚进去。林晚点下提交，审核通过，文章正式发布，"
        "随后把文章链接发给季晴，季晴点开读完。她把通知发送到公司人事邮箱，"
        "辞职生效，公司同事评论她已经离职。"
    )
    return core + "她急着赶完所有事情，屏幕的冷光一遍遍落在手背上。" * 15


class _FakeClient:
    def chat_completion(self, messages, *, completion_metadata_sink=None, **kwargs):
        if completion_metadata_sink:
            completion_metadata_sink(
                {"finish_reason": "stop", "input_tokens": 100, "output_tokens": 700}
            )
        content = messages[-1]["content"]
        return _good_text() if "本小节世界运行边界" in content else _bad_text()


def test_manifest_freezes_eight_balanced_allowlisted_samples(tmp_path):
    manifest = experiment.build(tmp_path)
    audit = experiment.audit(tmp_path)

    assert manifest["sample_count"] == 8
    assert {item["arm"] for item in manifest["samples"]} == {"A", "B"}
    assert sum(item["arm"] == "A" for item in manifest["samples"]) == 4
    assert sum(item["arm"] == "B" for item in manifest["samples"]) == 4
    assert all(
        item["runtime_observation"]["injected"] == (item["arm"] == "B")
        for item in manifest["samples"]
    )
    assert manifest["production_behavior_changed"] is False
    assert manifest["silent_reruns_allowed"] is False
    assert audit["common_input_invariant"] is True
    assert audit["runtime_prompt_token_delta_mean"] > 0


def test_attempt_ledger_forbids_silent_rerun_and_evaluation_cannot_promote(
    tmp_path, monkeypatch
):
    experiment.build(tmp_path)
    monkeypatch.setattr(experiment.settings, "LLM_API_KEY", "fixture-key")
    monkeypatch.setattr(experiment.settings, "WRITER_WORLD_RUNTIME_MODE", "off")
    monkeypatch.setattr(experiment, "get_llm_client", lambda model: _FakeClient())

    run_result = experiment.run(tmp_path)
    evaluation = experiment.evaluate(tmp_path)
    ledger = json.loads((tmp_path / "attempt-ledger.json").read_text(encoding="utf-8"))

    assert run_result == {"status": "complete", "attempted": 8, "retries": 0}
    assert all(item["attempt_count"] == 1 for item in ledger["samples"].values())
    assert evaluation["aggregate"]["B"]["hard_reality_violation_count"] < evaluation["aggregate"]["A"]["hard_reality_violation_count"]
    assert evaluation["gates"]["must_event_retention_non_inferior"] is True
    assert evaluation["gates"]["sample_size_sufficient"] is False
    assert evaluation["gates"]["human_prose_review_complete"] is False
    assert evaluation["promotion_eligible"] is False

    with pytest.raises(RuntimeError, match="refusing silent rerun"):
        experiment.run(tmp_path)


def test_build_refuses_to_replace_an_existing_attempt_ledger(tmp_path):
    experiment.build(tmp_path)
    with pytest.raises(FileExistsError, match="attempt ledger exists"):
        experiment.build(tmp_path)
