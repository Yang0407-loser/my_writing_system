import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary import external_runner_wr2c3
from experiments.world_runtime_writer_canary import semantic_canary_wr2c3 as canary


_TYPES = (
    "storefront_public_sale", "storefront_public_handoff", "knowledge_state",
    "resignation_acknowledgement", "unsourced_project_fact", "object_state",
    "repeated_completed_event", "employment_state", "publication_state",
    "resignation_delivery", "resignation_personal_record", "clock_state",
    "location_state",
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
        "schema_version": "world-runtime-semantic-canary-wr2c3-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "locked_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "external_runner_sha256": hashlib.sha256(external_runner_wr2c3.SOURCE.read_bytes()).hexdigest(),
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


def test_build_freezes_development_v3_without_old_or_holdout_reuse(tmp_path):
    manifest = canary.build(tmp_path)
    audit = canary.audit(tmp_path)

    assert manifest["sample_count"] == 20
    assert manifest["expected_change_count"] == 16
    assert manifest["expected_empty_count"] == 5
    assert manifest["all_13_types_covered"] is True
    assert manifest["partition_role"] == "visible_development_v3_not_holdout"
    assert manifest["prior_development_partition_reused"] is False
    assert manifest["sealed_holdout_used"] is False
    assert audit["status"] == "ready_zero_call_external_execution_not_authorized"
    assert audit["pending"] == 20
    assert audit["attempt_count_total"] == audit["output_files"] == 0
    for sample in manifest["samples"]:
        rendered = json.dumps(sample["messages"], ensure_ascii=False)
        assert sample["text"] in rendered
        assert "expected_changes" not in rendered
        assert '"expected_validation":' not in rendered
        schema = sample["messages"][1]["content"].split("Return exactly:")[1]
        assert '"subject":' not in schema
        assert '"mechanism":' not in schema
        assert '"actor":' not in schema
        assert '"predicate":' not in schema


def test_hash_bound_authorization_blocks_old_partition_holdout_or_commit(tmp_path):
    canary.build(tmp_path)
    missing = external_runner_wr2c3.preflight(tmp_path, tmp_path / "missing.json")
    assert missing["ready"] is False

    authorization = _authorization(tmp_path)
    path = tmp_path / "authorization.json"
    _write(path, authorization)
    ready = external_runner_wr2c3.preflight(tmp_path, path)
    assert ready["ready"] is True
    assert ready["prior_development_partition_reused"] is False
    assert ready["sealed_holdout_used"] is False
    assert ready["state_commit_authorized"] is False

    authorization["state_commit_authorized"] = True
    _write(path, authorization)
    blocked = external_runner_wr2c3.preflight(tmp_path, path)
    assert blocked["ready"] is False
    assert any("state_commit_authorized" in issue for issue in blocked["issues"])


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


def test_fake_run_consumes_twenty_once_fails_closed_and_cannot_be_repeated(tmp_path, monkeypatch):
    canary.build(tmp_path)
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    fake = _FakeClient()
    monkeypatch.setattr(external_runner_wr2c3, "get_llm_client", lambda model: fake)

    result = external_runner_wr2c3.run_once(tmp_path, authorization_path)
    ledger = _read(tmp_path / "attempt-ledger.json")
    evaluation = _read(tmp_path / "evaluation.json")

    assert fake.calls == 20
    assert result["attempt_count_total"] == 20
    assert result["development_gate_passed"] is False
    assert result["new_unseen_holdout_authorized"] is False
    assert result["production_promotion_eligible"] is False
    assert result["state_commit_authorized"] is False
    assert all(item["attempt_count"] == 1 for item in ledger["samples"].values())
    assert evaluation["semantic_recall"] == 0.0
    assert evaluation["state_mutations"] == evaluation["commits"] == 0

    with pytest.raises(RuntimeError, match="external_preflight_failed"):
        external_runner_wr2c3.run_once(tmp_path, authorization_path)
