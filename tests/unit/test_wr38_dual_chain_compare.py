import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary import wr38_dual_chain_compare as compare


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _authorization(tmp_path):
    manifest_path = tmp_path / "private/locked-manifest.json"
    manifest = _read(manifest_path)
    return {
        "schema_version": "world-runtime-wr38-dual-chain-external-authorization-v1",
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


def test_build_and_preflight_with_authorization(tmp_path):
    compare.build(tmp_path)
    missing = compare.preflight(tmp_path, tmp_path / "missing.json")
    assert missing["ready"] is False
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    ready = compare.preflight(tmp_path, authorization_path)
    assert ready["ready"] is True
    assert ready["model_calls"] == 3


def test_legacy_time_parsing_handles_digital_and_chinese():
    bundle = {
        "changes": [
            {"category": "temporal_state", "subject": "世界时钟", "predicate": "time",
             "value": "05:25", "status": "confirmed"},
            {"category": "temporal_state", "subject": "世界时钟", "predicate": "time",
             "value": "六点差五分", "status": "confirmed"},
            {"category": "character_state", "subject": "林晚", "predicate": "状态",
             "value": "x", "status": "confirmed"},
        ]
    }
    times = compare._legacy_times(bundle)
    parsed = {item["parsed_time"] for item in times}
    assert parsed == {"05:25", "05:55"}


def test_compare_reports_clock_divergence(tmp_path, monkeypatch):
    outputs = tmp_path / "private/outputs"
    commits = tmp_path / "commits"
    _write(outputs / "S1.bundle.json", {"changes": [
        {"category": "temporal_state", "subject": "时钟", "predicate": "time",
         "value": "05:07", "status": "confirmed"},
        {"category": "temporal_state", "subject": "时钟", "predicate": "time",
         "value": "05:25", "status": "confirmed"},
        {"category": "temporal_state", "subject": "时钟", "predicate": "time",
         "value": "05:30", "status": "confirmed"},
    ]})
    _write(commits / "S1.json", {"ledger": {"entries": [
        {"change_type": "clock_state", "after_value": "05:30"},
    ]}})
    for subsection in (2, 3):
        _write(outputs / f"S{subsection}.bundle.json", {"changes": []})
        _write(commits / f"S{subsection}.json", {"ledger": {"entries": [
            {"change_type": "clock_state", "after_value": "06:00"},
        ]}})
    monkeypatch.setattr(compare, "CANARY_COMMITS", commits)
    report = compare.compare_subsections(tmp_path)
    assert report["totals"]["clock_matched"] == 1
    assert report["totals"]["clock_legacy_only"] == 2
    assert report["totals"]["clock_wr_only"] == 2
    assert report["subsections"][0]["clock_divergence"]["legacy_only"] == ["05:07", "05:25"]


class _FakeClient:
    def __init__(self):
        self.calls = 0

    def chat_completion(self, messages, **kwargs):
        self.calls += 1
        assert kwargs["json_mode"] is True
        return json.dumps({"changes": []}, ensure_ascii=False)


def test_fake_run_once_calls_three_times_and_writes_report(tmp_path, monkeypatch):
    compare.build(tmp_path)
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    fake = _FakeClient()
    monkeypatch.setattr(compare, "get_llm_client", lambda model: fake)
    report = compare.run_once(tmp_path, authorization_path)
    assert fake.calls == 3
    assert all(record["status"] == "succeeded" for record in report["records"])
    assert (tmp_path / "private/outputs/S1.bundle.json").exists()
    assert (tmp_path / "report.json").exists()
    with pytest.raises(RuntimeError, match="preflight_failed"):
        compare.run_once(tmp_path, authorization_path)
