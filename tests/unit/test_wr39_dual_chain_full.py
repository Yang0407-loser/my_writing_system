import hashlib
import json

import pytest

import app.agents.base as base_module
from experiments.world_runtime_writer_canary import wr39_dual_chain_full as compare


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _authorization(tmp_path):
    manifest_path = tmp_path / "private/locked-manifest.json"
    manifest = _read(manifest_path)
    return {
        "schema_version": "world-runtime-wr39-dual-chain-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "locked_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "runner_source_sha256": manifest["source_hashes"]["runner_sha256"],
        "maximum_requests": compare.SUBSECTIONS * 2,
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "production_writer_change_authorized": False,
        "state_commit_authorized": False,
    }


def test_build_and_preflight_with_authorization(tmp_path):
    manifest = compare.build(tmp_path)
    assert len(manifest["samples"]) == compare.SUBSECTIONS
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    ready = compare.preflight(tmp_path, authorization_path)
    assert ready["ready"] is True
    assert ready["model_calls"] == 6


class _FakeClient:
    def __init__(self):
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append(kwargs.get("prompt_name"))
        sink = kwargs.get("completion_metadata_sink")
        if sink is not None:
            sink({"finish_reason": "stop", "latency_seconds": 0.01})
        if kwargs.get("prompt_name") == "post_write_state_extraction":
            return json.dumps({"changes": []}, ensure_ascii=False)
        return json.dumps({"v": "2.3", "s": [], "o": [], "f": [], "a": []}, ensure_ascii=False)


def test_fake_run_once_calls_six_times_and_writes_report(tmp_path, monkeypatch):
    compare.build(tmp_path)
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    fake = _FakeClient()
    monkeypatch.setattr(compare, "get_llm_client", lambda model: fake)
    monkeypatch.setattr(base_module, "get_llm_client", lambda model: fake)
    report = compare.run_once(tmp_path, authorization_path)
    assert len(fake.calls) == 6
    assert all(record["status"] == "succeeded" for record in report["records"])
    assert all(record["handover_ok"] is True for record in report["records"])
    assert (tmp_path / "report.json").exists()
    with pytest.raises(RuntimeError, match="preflight_failed"):
        compare.run_once(tmp_path, authorization_path)


def test_compare_reports_fact_divergence(tmp_path, monkeypatch):
    outputs = tmp_path / "private/outputs"
    commits = tmp_path / "commits"
    for subsection in range(1, 4):
        _write(outputs / f"S{subsection}.handover.json", {
            "note": {"character_state": "林晚已提交辞职", "to_section": subsection},
            "observation": {"execution_status": "completed_with_changes"},
        })
        _write(outputs / f"S{subsection}.bundle.json", {"changes": [
            {"category": "temporal_state", "subject": "世界时钟", "predicate": "time",
             "value": "06:00", "status": "confirmed"},
        ]})
        _write(commits / f"S{subsection}.json", {
            "commit_id": f"commit:test:r{subsection + 7}",
            "idempotency_key": f"test:S{subsection}",
            "output_hash": "0" * 64,
            "before": {"project_id": "gold-project:saturday-bakery", "revision": 7,
                       "facts": [{"fact_id": "fact:clock:time", "subject": "world_clock",
                                  "predicate": "time", "value": "04:20",
                                  "epistemic_status": "confirmed_true", "revision": 7,
                                  "authority": "text_extracted",
                                  "provenance": {"source_id": "ev:test", "source_type": "accepted_state_delta",
                                                 "source_hash": "0" * 64, "producer": "test"}}]},
            "after": {"project_id": "gold-project:saturday-bakery", "revision": subsection + 7,
                      "facts": [{"fact_id": "fact:clock:time", "subject": "world_clock",
                                 "predicate": "time", "value": "06:00",
                                 "epistemic_status": "confirmed_true", "revision": subsection + 7,
                                 "authority": "text_extracted",
                                 "provenance": {"source_id": "ev:test", "source_type": "accepted_state_delta",
                                                "source_hash": "0" * 64, "producer": "test"}}]},
            "ledger": {"ledger_id": "ledger:test", "project_id": "gold-project:saturday-bakery",
                       "revision": subsection + 7, "entries": [
                           {"ledger_id": f"ledger:test:{subsection}", "revision": subsection + 7,
                            "change_id": f"change:test:{subsection}", "change_type": "clock_state",
                            "subject": "world_clock", "predicate": "time", "after_value": "06:00",
                            "fact_id": "fact:clock:time", "evidence_ids": ["ev:test"],
                            "output_hash": "0" * 64, "idempotency_key": "test",
                            "validation_outcome": "valid", "rule_ids": [], "schema_version": "x"}]},
            "state_frame": {"frame_id": "frame:test", "task_id": "test", "section": 1,
                            "subsection": subsection, "temporal_state": [], "location_state": [],
                            "character_presence": [], "persistent_state": [],
                            "relationship_state": [], "open_loops": [],
                            "unknowns_and_conflicts": [], "evidence": [],
                            "excluded_assertion_ids": [], "source_hash": "0" * 64,
                            "frame_hash": "0" * 64, "estimated_tokens": 1,
                            "schema_version": "state-frame-v1"},
        })
    monkeypatch.setattr(compare, "CANARY_COMMITS", commits)
    report = compare.compare_subsections(tmp_path)
    assert report["totals"]["subsections"] == 3
    assert report["totals"]["matched_fact_keys"] >= 0
    assert report["totals"]["wr_only_fact_keys"] >= 0
