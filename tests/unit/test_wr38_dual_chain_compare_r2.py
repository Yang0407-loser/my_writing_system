import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary import wr38_dual_chain_compare_r2 as compare


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _authorization(tmp_path):
    manifest_path = tmp_path / "private/locked-manifest.json"
    manifest = _read(manifest_path)
    return {
        "schema_version": "world-runtime-wr38r2-dual-chain-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "locked_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "runner_source_sha256": manifest["source_hashes"]["runner_sha256"],
        "maximum_requests": compare.SUBSECTIONS,
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "production_writer_change_authorized": False,
        "state_commit_authorized": False,
    }


def test_provider_uses_larger_output_cap():
    assert compare.PROVIDER["max_tokens"] == 4000


def test_build_and_preflight_with_authorization(tmp_path):
    manifest = compare.build(tmp_path)
    assert "wr38r2" in manifest["experiment_id"]
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    ready = compare.preflight(tmp_path, authorization_path)
    assert ready["ready"] is True
    assert ready["model_calls"] == 3


class _FakeClient:
    def __init__(self):
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append(kwargs.get("max_tokens"))
        kwargs["completion_metadata_sink"]({"finish_reason": "stop", "latency_seconds": 0.01})
        return json.dumps({"changes": []}, ensure_ascii=False)


def test_fake_run_once_calls_with_4000_tokens(tmp_path, monkeypatch):
    compare.build(tmp_path)
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    fake = _FakeClient()
    monkeypatch.setattr(compare, "get_llm_client", lambda model: fake)
    report = compare.run_once(tmp_path, authorization_path)
    assert fake.calls == [4000, 4000, 4000]
    assert all(record["finish_reason"] == "stop" for record in report["records"])
    assert (tmp_path / "report.json").exists()
    with pytest.raises(RuntimeError, match="preflight_failed"):
        compare.run_once(tmp_path, authorization_path)
