import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary import external_runner_wr1p as runner
from experiments.world_runtime_writer_canary import prose_canary_wr1p as wr1p


def _authorization(output_dir):
    manifest = output_dir / "private/locked-manifest.json"
    return {
        "schema_version": "world-runtime-wr1p-external-authorization-v1",
        "authorized": True,
        "experiment_id": json.loads(manifest.read_text(encoding="utf-8"))["experiment_id"],
        "locked_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "external_runner_sha256": hashlib.sha256(runner.SOURCE.read_bytes()).hexdigest(),
        "maximum_requests": 8,
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "production_writer_change_authorized": False,
        "wr2_authorized": False,
    }


def _write_authorization(output_dir, payload):
    path = output_dir / "private/external-execution-authorization.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class _FakeClient:
    def __init__(self):
        self.calls = 0

    def chat_completion(self, messages, *, completion_metadata_sink=None, **kwargs):
        self.calls += 1
        assert kwargs["max_retries"] == 0
        if completion_metadata_sink:
            completion_metadata_sink({"finish_reason": "stop"})
        content = messages[-1]["content"]
        runtime = "本小节世界运行边界" in content
        if "早班车" in content:
            core = "林晚进入操作间。她把钱退回去，说六点再来。" if runtime else "林晚进入操作间。周野收下现金，趁热装两个，把纸袋放在门外。"
        elif "两个对话" in content:
            core = "季晴问她草稿。林晚把正文发到工作群，同事回复说第二段有个错字。" if runtime else "季晴问她草稿。工作群同事说正文里的结尾太急，却没有发送过程。"
        elif "绿豆汤碗" in content:
            core = "她等待回复，只查看已发布和已送达。家中无人，绿豆汤仍在碗里。" if runtime else "她等待消息，又点下发布。家中无人，茶几上只剩空碗。"
        else:
            core = "人事尚未回复，她仍然在职，决定照常去公司。" if runtime else "人事尚未确认，她却说自己已经正式离职，今天不去公司。"
        return core + "烤箱低低作响，窗上的水汽缓慢滑落，她把手边的动作做完。" * 24


def test_preflight_requires_hash_bound_authorization(tmp_path, monkeypatch):
    monkeypatch.setattr(wr1p.settings, "WRITER_WORLD_RUNTIME_MODE", "off")
    wr1p.build(tmp_path)
    authorization = _authorization(tmp_path)
    authorization["locked_manifest_sha256"] = "0" * 64
    path = _write_authorization(tmp_path, authorization)

    result = runner.preflight(tmp_path, path)

    assert result["ready"] is False
    assert any("locked_manifest_sha256" in issue for issue in result["issues"])


def test_run_once_makes_exactly_eight_zero_retry_calls_then_evaluates(tmp_path, monkeypatch):
    monkeypatch.setattr(wr1p.settings, "WRITER_WORLD_RUNTIME_MODE", "off")
    wr1p.build(tmp_path)
    path = _write_authorization(tmp_path, _authorization(tmp_path))
    client = _FakeClient()
    monkeypatch.setattr(runner, "get_llm_client", lambda model: client)

    preflight = runner.preflight(tmp_path, path)
    result = runner.run_once(tmp_path, path)
    ledger = json.loads((tmp_path / "attempt-ledger.json").read_text(encoding="utf-8"))

    assert preflight["ready"] is True
    assert client.calls == 8
    assert result["succeeded"] == 8
    assert result["attempt_count_total"] == 8
    assert result["transport_retries"] == 0
    assert result["machine_gate_passed"] is True
    assert result["single_owner_review_required"] is True
    assert all(item["status"] == "succeeded" for item in ledger["samples"].values())
    with pytest.raises(RuntimeError, match="preflight_failed"):
        runner.run_once(tmp_path, path)
