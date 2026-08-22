import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary import external_runner_wr2c5
from experiments.world_runtime_writer_canary import semantic_canary_wr2c5 as canary


_TYPES = (
    "storefront_public_sale", "storefront_public_handoff", "storefront_operation_state",
    "knowledge_state", "resignation_acknowledgement", "unsourced_project_fact",
    "object_state", "repeated_completed_event", "employment_state", "publication_state",
    "resignation_delivery", "resignation_personal_record", "clock_state", "location_state",
)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _authorization(output_dir):
    manifest_path = output_dir / "private/locked-manifest.json"
    manifest = _read(manifest_path)
    return {
        "schema_version": "world-runtime-semantic-canary-wr2c5-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "locked_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "external_runner_sha256": hashlib.sha256(external_runner_wr2c5.SOURCE.read_bytes()).hexdigest(),
        "semantic_extractor_source_sha256": manifest["semantic_extractor_source_sha256"],
        "projector_source_sha256": manifest["projector_source_sha256"],
        "maximum_requests": manifest["sample_count"],
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "prior_development_partition_reuse_authorized": False,
        "sealed_holdout_use_authorized": False,
        "production_writer_change_authorized": False,
        "state_commit_authorized": False,
    }


def test_build_freezes_development_v5(tmp_path):
    manifest = canary.build(tmp_path)
    audit = canary.audit(tmp_path)
    assert manifest["sample_count"] == 23
    assert manifest["expected_change_count"] == 25
    assert manifest["expected_empty_count"] == 5
    assert manifest["all_14_types_covered"] is True
    assert manifest["partition_role"] == "visible_development_v5_not_holdout"
    assert audit["status"] == "ready_zero_call_external_execution_not_authorized"
    assert audit["pending"] == 23
    for sample in manifest["samples"]:
        rendered = json.dumps(sample["messages"], ensure_ascii=False)
        assert "expected_changes" not in rendered
        assert '"expected_validation":' not in rendered


def test_hash_bound_authorization(tmp_path):
    canary.build(tmp_path)
    missing = external_runner_wr2c5.preflight(tmp_path, tmp_path / "missing.json")
    assert missing["ready"] is False
    authorization = _authorization(tmp_path)
    path = tmp_path / "authorization.json"
    _write(path, authorization)
    ready = external_runner_wr2c5.preflight(tmp_path, path)
    assert ready["ready"] is True
    assert ready["state_commit_authorized"] is False


class _FakeClient:
    def __init__(self):
        self.calls = 0

    def chat_completion(self, messages, **kwargs):
        self.calls += 1
        assert kwargs["max_retries"] == 0
        assert kwargs["json_mode"] is True
        kwargs["completion_metadata_sink"]({"latency_seconds": 0.01})
        judgments = [
            {
                "change_type": change_type,
                "occurred": False,
                "after_value": None,
                "mode": "actual",
                "epistemic": "asserted",
                "evidence": [],
            }
            for change_type in _TYPES
        ]
        return json.dumps({"judgments": judgments}, ensure_ascii=False)


def test_fake_run_once_fails_closed_and_cannot_repeat(tmp_path, monkeypatch):
    canary.build(tmp_path)
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    fake = _FakeClient()
    monkeypatch.setattr(external_runner_wr2c5, "get_llm_client", lambda model: fake)
    result = external_runner_wr2c5.run_once(tmp_path, authorization_path)
    assert fake.calls == 23
    assert result["attempt_count_total"] == 23
    assert result["development_gate_passed"] is False
    assert result["state_commit_authorized"] is False
    with pytest.raises(RuntimeError, match="external_preflight_failed"):
        external_runner_wr2c5.run_once(tmp_path, authorization_path)
