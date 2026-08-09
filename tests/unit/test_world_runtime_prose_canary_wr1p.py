import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary import prose_canary_wr1p as wr1p


def test_build_and_audit_freeze_eight_zero_call_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(wr1p.settings, "WRITER_WORLD_RUNTIME_MODE", "off")
    manifest = wr1p.build(tmp_path)
    audit = wr1p.audit(tmp_path)
    ledger = json.loads((tmp_path / "attempt-ledger.json").read_text(encoding="utf-8"))

    assert manifest["sample_count"] == 8
    assert manifest["scene_count"] == 4
    assert manifest["external_generation_authorized"] is False
    assert manifest["single_owner_review_only"] is True
    assert audit["status"] == "ready_zero_call_external_generation_not_authorized"
    assert audit["pending"] == 8
    assert audit["attempt_count_total"] == 0
    assert audit["output_files"] == 0
    assert audit["provider_calls_executed"] == 0
    assert audit["runtime_prompt_token_delta_mean"] > 0
    assert audit["runtime_prompt_token_delta_mean"] <= 1100
    assert sum(item["attempt_count"] for item in ledger["samples"].values()) == 0
    for scene_id in {item["scene_id"] for item in manifest["samples"]}:
        pair = [item for item in manifest["samples"] if item["scene_id"] == scene_id]
        assert {item["arm"] for item in pair} == {"A", "B"}
        assert len({item["common_input_hash"] for item in pair}) == 1


def test_module_has_no_provider_run_entrypoint():
    assert not hasattr(wr1p, "run")


def test_build_refuses_to_replace_attempt_ledger(tmp_path):
    wr1p.build(tmp_path)
    with pytest.raises(FileExistsError, match="refusing rebuild"):
        wr1p.build(tmp_path)


def test_audit_rejects_frozen_fixture_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(wr1p.settings, "WRITER_WORLD_RUNTIME_MODE", "off")
    wr1p.build(tmp_path)
    manifest_path = tmp_path / "private/locked-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evaluation_contract_hash"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RuntimeError, match="wr1p_frozen_drift:evaluation_contract_hash"):
        wr1p.audit(tmp_path)


def _fill(text: str) -> str:
    return text + "烤箱低低作响，窗上的水汽缓慢滑落，她没有急着解释，只把手边的动作做完。" * 24


def test_evaluate_uses_v2_and_requires_only_one_owner_review_after_machine_pass(tmp_path):
    manifest = wr1p.build(tmp_path)
    ledger_path = tmp_path / "attempt-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    for sample in manifest["samples"]:
        runtime = sample["arm"] == "B"
        scene = sample["scene_id"]
        if scene == "adversarial-storefront-hours":
            core = (
                "林晚进入操作间。她把钱退回去，说六点再来。"
                if runtime
                else "林晚进入操作间。周野收下现金，趁热装两个，把纸袋放在门外。"
            )
        elif scene == "adversarial-unpublished-knowledge":
            core = (
                "季晴问她草稿。林晚把正文发到工作群，同事回复说第二段有个错字。"
                if runtime
                else "季晴问她草稿。工作群同事说正文里的结尾太急，却没有发送过程。"
            )
        elif scene == "adversarial-object-and-repeat":
            core = (
                "她等待回复，只查看已发布和已送达。家中无人，绿豆汤仍在碗里。"
                if runtime
                else "她等待消息，又点下发布。家中无人，茶几上只剩空碗。"
            )
        else:
            core = (
                "人事尚未回复，她仍然在职，决定照常去公司。"
                if runtime
                else "人事尚未确认，她却说自己已经正式离职，今天不去公司。"
            )
        text = _fill(core)
        output = tmp_path / "private/outputs" / f"{sample['sample_id']}.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        ledger["samples"][sample["sample_id"]].update(
            status="succeeded",
            attempt_count=1,
            output_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")

    result = wr1p.evaluate(tmp_path)

    assert result["machine_gate_passed"] is True
    assert result["single_owner_review_required"] is True
    assert result["gates"]["single_owner_prose_review_complete"] is False
    assert result["production_promotion_eligible"] is False
    assert result["real_task_canary_authorized"] is False
    assert result["decision"] == "machine_pass_pending_single_owner_review"
    assert result["aggregate"]["A"]["scenes_with_hard_violation"] >= 2
    assert result["aggregate"]["B"]["hard_violation_count"] == 0
