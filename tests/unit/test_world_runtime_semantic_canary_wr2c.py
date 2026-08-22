import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary import external_runner_wr2c
from experiments.world_runtime_writer_canary import semantic_canary_wr2c as canary


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _authorization(output_dir):
    manifest_path = output_dir / "private/locked-manifest.json"
    manifest = _read(manifest_path)
    return {
        "schema_version": "world-runtime-semantic-canary-wr2c-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "locked_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "external_runner_sha256": hashlib.sha256(external_runner_wr2c.SOURCE.read_bytes()).hexdigest(),
        "semantic_extractor_source_sha256": manifest["semantic_extractor_source_sha256"],
        "maximum_requests": manifest["sample_count"],
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "sealed_holdout_use_authorized": False,
        "production_writer_change_authorized": False,
        "state_commit_authorized": False,
    }


def test_zero_call_build_freezes_twenty_public_development_samples(tmp_path):
    manifest = canary.build(tmp_path)
    audit = canary.audit(tmp_path)

    assert manifest["sample_count"] == 20
    assert manifest["expected_change_count"] == 16
    assert manifest["expected_empty_count"] == 5
    assert manifest["all_13_types_covered"] is True
    assert manifest["partition_role"] == "visible_development_not_holdout"
    assert manifest["external_execution_authorized"] is False
    assert manifest["sealed_holdout_used"] is False
    assert audit["status"] == "ready_zero_call_external_execution_not_authorized"
    assert audit["pending"] == 20
    assert audit["attempt_count_total"] == audit["output_files"] == audit["provider_calls_executed"] == 0
    for sample in manifest["samples"]:
        rendered = json.dumps(sample["messages"], ensure_ascii=False)
        assert sample["text"] in rendered
        assert "expected_changes" not in rendered
        assert "expected_validation\":" not in rendered


def test_build_refuses_existing_attempt_ledger(tmp_path):
    canary.build(tmp_path)
    with pytest.raises(FileExistsError, match="attempt ledger exists"):
        canary.build(tmp_path)


def test_external_preflight_requires_hash_bound_authorization(tmp_path):
    canary.build(tmp_path)
    missing = external_runner_wr2c.preflight(tmp_path, tmp_path / "missing.json")
    assert missing["ready"] is False
    assert "authorization_missing" in missing["issues"]

    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    ready = external_runner_wr2c.preflight(tmp_path, authorization_path)
    assert ready["ready"] is True
    assert ready["sample_count"] == 20
    assert ready["transport_retries"] == 0
    assert ready["sealed_holdout_used"] is False
    assert ready["state_commit_authorized"] is False


class _FakeClient:
    def __init__(self):
        self.calls = 0

    def chat_completion(self, messages, **kwargs):
        self.calls += 1
        assert kwargs["max_retries"] == 0
        assert kwargs["json_mode"] is True
        kwargs["completion_metadata_sink"]({
            "finish_reason": "stop", "input_tokens": 100, "output_tokens": 5, "latency_seconds": 0.01,
        })
        return '{"events":[]}'


def test_fake_external_run_consumes_each_request_once_and_never_promotes(tmp_path, monkeypatch):
    canary.build(tmp_path)
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    fake = _FakeClient()
    monkeypatch.setattr(external_runner_wr2c, "get_llm_client", lambda model: fake)

    result = external_runner_wr2c.run_once(tmp_path, authorization_path)
    ledger = _read(tmp_path / "attempt-ledger.json")
    evaluation = _read(tmp_path / "evaluation.json")

    assert fake.calls == 20
    assert result["command_executed_exactly_once"] is True
    assert result["attempt_count_total"] == 20
    assert result["transport_retries"] == 0
    assert result["development_gate_passed"] is False
    assert result["production_promotion_eligible"] is False
    assert result["sealed_holdout_authorized"] is False
    assert result["state_commit_authorized"] is False
    assert all(item["attempt_count"] == 1 and item["status"] == "succeeded" for item in ledger["samples"].values())
    assert evaluation["semantic_recall"] == 0.0
    assert evaluation["state_mutations"] == evaluation["commits"] == 0

    with pytest.raises(RuntimeError, match="external_preflight_failed"):
        external_runner_wr2c.run_once(tmp_path, authorization_path)


def test_authorization_cannot_enable_holdout_production_or_commit(tmp_path):
    canary.build(tmp_path)
    authorization = _authorization(tmp_path)
    authorization["state_commit_authorized"] = True
    path = tmp_path / "authorization.json"
    _write(path, authorization)

    result = external_runner_wr2c.preflight(tmp_path, path)
    assert result["ready"] is False
    assert any("state_commit_authorized" in issue for issue in result["issues"])

