import json

import pytest

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r


def _fit(text):
    return text + "烤箱发出低低的嗡鸣，窗上的水汽缓慢向下滑，她停了一会儿才继续手里的事。" * 12


class _FakeClient:
    def chat_completion(self, messages, *, completion_metadata_sink=None, **kwargs):
        if completion_metadata_sink:
            completion_metadata_sink(
                {"finish_reason": "stop", "input_tokens": 100, "output_tokens": 650}
            )
        content = messages[-1]["content"]
        runtime = "本小节世界运行边界" in content
        if "提前购买要求" in content:
            core = (
                "林晚从侧门进入操作间。路人继续敲门，周野隔着临街门说明六点再来，两人继续制作。"
                if runtime
                else "林晚进入操作间。路人敲门，周野收下现金，把临街门拉开让来客进来买了面包。"
            )
        elif "工作群里必须有人" in content:
            core = (
                "季晴问她草稿。林晚把一段正文发到工作群，同事读后指出文章里的一个错字。"
                if runtime
                else "季晴问她草稿。工作群里的同事已经知道文章正文，还指出文章里的一个错字。评论区已经有了点赞。"
            )
        elif "再次确认文章发布" in content:
            core = (
                "她在店里等待人事消息，只查看已发布页面和已发送回执。镜头切回家中，绿豆汤仍在碗里。"
                if runtime
                else "她在店里等待消息，又点下发布，页面显示发布成功。家里没有人，茶几上只剩空碗。"
            )
        else:
            core = (
                "上班闹钟响了，同事问她是否到岗。她说明人事没有回复，自己仍在职，今天照常处理工作。"
                if runtime
                else "上班闹钟响了，同事问她是否到岗。人事没有回复，她却说自己已经正式离职。"
            )
        return _fit(core)


def _scene(scene_id):
    fixture = json.loads(wr1r.FIXTURE.read_text(encoding="utf-8"))
    return next(item for item in fixture["scenes"] if item["scene_id"] == scene_id)


def test_build_freezes_four_paired_adversarial_scenes(tmp_path):
    manifest = wr1r.build(tmp_path)
    audit = wr1r.audit(tmp_path)

    assert manifest["sample_count"] == 8
    assert manifest["scene_count"] == 4
    assert manifest["evaluation_contract_authored_before_generation"] is True
    assert manifest["evaluator_source_sha256"]
    assert manifest["evaluation_contract_hash"]
    assert manifest["production_behavior_changed"] is False
    assert manifest["silent_reruns_allowed"] is False
    for scene_id in {item["scene_id"] for item in manifest["samples"]}:
        pair = [item for item in manifest["samples"] if item["scene_id"] == scene_id]
        assert len(pair) == 2
        assert {item["arm"] for item in pair} == {"A", "B"}
        assert len({item["common_input_hash"] for item in pair}) == 1
        assert [item["runtime_observation"]["injected"] for item in pair].count(True) == 1
    assert audit["paired_common_input_invariant"] is True
    assert audit["frozen_evaluator_integrity"] is True
    assert audit["runtime_prompt_token_delta_mean"] > 0


def test_storefront_check_distinguishes_internal_access_from_public_sale():
    scene = _scene("adversarial-storefront-hours")
    good = "林晚从侧门进入操作间。路人继续敲门，周野隔着门说六点再来，两人回去看烤箱。"
    bad = "林晚进入操作间。路人敲门，周野把临街门拉开让来客进来买了两只面包。"

    assert wr1r.evaluate_text(scene, good)["hard_reality_violation_count"] == 0
    assert wr1r.evaluate_text(scene, bad)["hard_reality_violations"][
        "storefront_public_open_before_0600"
    ] is True


def test_unpublished_knowledge_checks_reaction_and_missing_path():
    scene = _scene("adversarial-unpublished-knowledge")
    bad = "草稿还没发布，评论区已经有了点赞。季晴问起文章，公司群里的同事都知道正文写的是什么。"
    result = wr1r.evaluate_text(scene, bad)

    assert result["hard_reality_violations"]["public_reaction_before_publication"] is True
    assert result["hard_reality_violations"]["coworker_knows_without_transmission_path"] is True

    negated = "季晴问起草稿，同事不知道文章正文。林晚把一段文字发到工作群，同事随后指出文章里的错字。"
    assert wr1r.evaluate_text(scene, negated)["hard_reality_violation_count"] == 0


def test_object_and_completed_event_checks_require_actor_or_new_instance():
    scene = _scene("adversarial-object-and-repeat")
    bad = "她在店里等着消息。家里没有人，茶几上只剩空碗。她又点下发布，页面显示发布成功。"
    good = "她在店里等着消息。镜头切回家中，绿豆汤仍在碗里，屋里没有人。"

    bad_result = wr1r.evaluate_text(scene, bad)
    assert bad_result["hard_reality_violations"]["object_changes_without_actor"] is True
    assert bad_result["hard_reality_violations"]["completed_event_repeated"] is True
    assert wr1r.evaluate_text(scene, good)["hard_reality_violation_count"] == 0


def test_employment_check_does_not_treat_delivery_as_termination():
    scene = _scene("adversarial-employment-transition")
    bad = "上班闹钟响了，同事问她是否到岗。她想自己已经正式离职，人事仍没有回复。"
    good = "上班闹钟响了，同事问她是否到岗。人事没有回复，她只说今天会照常处理交接。"

    assert wr1r.evaluate_text(scene, bad)["hard_reality_violations"][
        "employment_ended_without_acknowledgement"
    ] is True
    assert wr1r.evaluate_text(scene, good)["hard_reality_violation_count"] == 0

    negated = "同事问她是不是已经离职。林晚回复说并没有，人事尚未确认，她今天照常到岗。"
    assert wr1r.evaluate_text(scene, negated)["hard_reality_violation_count"] == 0


def test_build_refuses_to_replace_attempt_ledger(tmp_path):
    wr1r.build(tmp_path)
    with pytest.raises(FileExistsError, match="attempt ledger exists"):
        wr1r.build(tmp_path)


def test_end_to_end_diagnostic_requires_baseline_activation_and_never_promotes(
    tmp_path, monkeypatch
):
    wr1r.build(tmp_path)
    monkeypatch.setattr(wr1r.settings, "LLM_API_KEY", "fixture-key")
    monkeypatch.setattr(wr1r.settings, "WRITER_WORLD_RUNTIME_MODE", "off")
    monkeypatch.setattr(wr1r, "get_llm_client", lambda model: _FakeClient())

    run_result = wr1r.run(tmp_path)
    evaluation = wr1r.evaluate(tmp_path)
    ledger = json.loads((tmp_path / "attempt-ledger.json").read_text(encoding="utf-8"))

    assert run_result == {"status": "complete", "attempted": 8, "retries": 0}
    assert sum(item["attempt_count"] for item in ledger["samples"].values()) == 8
    assert evaluation["gates"]["baseline_adversarial_activation"] is True
    assert evaluation["aggregate"]["A"]["hard_reality_violation_count"] > 0
    assert evaluation["aggregate"]["B"]["hard_reality_violation_count"] == 0
    assert evaluation["gates"]["runtime_hard_violation_count_lower"] is True
    assert evaluation["gates"]["must_event_retention_non_inferior"] is True
    assert evaluation["promotion_eligible"] is False
    assert evaluation["decision"] == "diagnostic_only_pending_owner_review"

    with pytest.raises(RuntimeError, match="refusing silent rerun"):
        wr1r.run(tmp_path)
