import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary import sealed_holdout_wr2c513 as holdout
from experiments.world_runtime_writer_canary import semantic_canary_wr2c511 as canary


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


def _write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _synthetic_holdout(tmp_path):
    fixture = _read(canary.FIXTURE)
    cases = []
    for case in fixture["cases"]:
        rewritten = dict(case)
        rewritten["text"] = case["text"] + "（复查稿）"
        rewritten["case_id"] = "SYN-" + case["case_id"]
        cases.append(rewritten)
    payload = {
        "schema_version": "world-runtime-sealed-holdout-wr2c513-v1",
        "partition_role": "sealed_unseen_holdout",
        "cases": cases,
    }
    path = tmp_path / "private" / "sealed-holdout-v1.json"
    _write(path, payload)
    return path


def _wire(tmp_path, monkeypatch):
    holdout_path = _synthetic_holdout(tmp_path)
    lock_path = tmp_path / "holdout-lock.json"
    monkeypatch.setattr(holdout, "HOLDOUT_PATH", holdout_path)
    monkeypatch.setattr(holdout, "LOCK_PATH", lock_path)
    return holdout_path, lock_path


def _authorization(tmp_path):
    manifest_path = tmp_path / "private" / "locked-manifest.json"
    manifest = _read(manifest_path)
    return {
        "schema_version": "world-runtime-semantic-canary-wr2c513-sealed-holdout-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "holdout_sha256": manifest["holdout_sha256"],
        "locked_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "runner_source_sha256": hashlib.sha256(holdout.SOURCE.read_bytes()).hexdigest(),
        "extractor_source_sha256": hashlib.sha256(holdout.EXTRACTOR.read_bytes()).hexdigest(),
        "projector_source_sha256": hashlib.sha256(holdout.PROJECTOR.read_bytes()).hexdigest(),
        "maximum_requests": manifest["sample_count"],
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "prior_development_partition_reuse_authorized": False,
        "sealed_holdout_use_authorized": True,
        "production_writer_change_authorized": False,
        "state_commit_authorized": False,
    }


def test_seal_accepts_validator_consistent_gold(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    lock = holdout.seal(tmp_path)
    assert lock["coverage"]["all_14_types_covered"] is True
    manifest = holdout.build(tmp_path)
    assert manifest["sample_count"] == lock["coverage"]["sample_count"]
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    ready = holdout.preflight(tmp_path, authorization_path)
    assert ready["ready"] is True


def test_seal_rejects_gold_validation_mismatch(tmp_path, monkeypatch):
    holdout_path, _ = _wire(tmp_path, monkeypatch)
    payload = _read(holdout_path)
    target = next(case for case in payload["cases"] if "PUBLISH-01" in case["case_id"])
    target["changes"][0]["expected_validation"] = "invalid"
    _write(holdout_path, payload)
    with pytest.raises(ValueError, match="gold validation mismatch"):
        holdout.seal(tmp_path)


def test_seal_rejects_reused_development_text(tmp_path, monkeypatch):
    holdout_path, _ = _wire(tmp_path, monkeypatch)
    fixture = _read(canary.FIXTURE)
    original = fixture["cases"][0]["text"]
    payload = _read(holdout_path)
    payload["cases"][0]["text"] = original
    _write(holdout_path, payload)
    with pytest.raises(ValueError, match="reuses a development/holdout text"):
        holdout.seal(tmp_path)


class _FakeClient:
    def __init__(self):
        self.calls = 0

    def chat_completion(self, messages, **kwargs):
        self.calls += 1
        assert kwargs["max_retries"] == 0
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


def test_fake_run_once_consumes_all_fails_closed_and_cannot_repeat(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    holdout.seal(tmp_path)
    manifest = holdout.build(tmp_path)
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    fake = _FakeClient()
    monkeypatch.setattr(holdout, "get_llm_client", lambda model: fake)

    result = holdout.run_once(tmp_path, authorization_path)
    ledger = _read(tmp_path / "attempt-ledger.json")
    assert fake.calls == manifest["sample_count"]
    assert result["attempt_count_total"] == manifest["sample_count"]
    assert result["sealed_holdout_gate_passed"] is False
    assert all(item["attempt_count"] == 1 for item in ledger["samples"].values())
    with pytest.raises(RuntimeError, match="preflight_failed"):
        holdout.run_once(tmp_path, authorization_path)
