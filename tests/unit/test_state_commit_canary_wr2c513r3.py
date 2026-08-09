import hashlib
import json

import pytest

from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from experiments.world_runtime_writer_canary import state_commit_canary_wr2c513r3 as canary


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


def _authorization(tmp_path):
    manifest_path = tmp_path / "private/locked-manifest.json"
    manifest = _read(manifest_path)
    return {
        "schema_version": "world-runtime-state-commit-canary-c21r2-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "locked_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "runner_source_sha256": manifest["source_hashes"]["runner_sha256"],
        "maximum_requests": canary.SUBSECTIONS * 2,
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "production_writer_change_authorized": False,
        "state_commit_authorized": False,
    }


def test_build_and_preflight_with_authorization(tmp_path):
    manifest = canary.build(tmp_path)
    assert manifest["subsection_count"] == 3
    assert manifest["model_calls_per_subsection"] == 2
    assert manifest["project_id"] == build_saturday_bakery_gold_fixture().state_before.project_id
    assert "c21r2" in manifest["experiment_id"]

    missing = canary.preflight(tmp_path, tmp_path / "missing.json")
    assert missing["ready"] is False
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    ready = canary.preflight(tmp_path, authorization_path)
    assert ready["ready"] is True
    assert ready["model_calls"] == 6
    assert ready["state_commit_authorized"] is False


def test_generation_messages_include_state_scene_and_previous():
    gold = build_saturday_bakery_gold_fixture()
    messages = canary.build_generation_messages(
        state=gold.state_before,
        previous_text="[PREVIOUS_SUBSECTION]",
        scene_index=0,
    )
    prompt = messages[1]["content"]
    assert gold.state_before.project_id in prompt
    assert "PREVIOUS_TEXT" in prompt
    assert "只输出本节中文小说正文" in prompt


class _FakeClient:
    def __init__(self):
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append(kwargs["prompt_name"])
        kwargs["completion_metadata_sink"]({"latency_seconds": 0.01})
        if kwargs["json_mode"]:
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
        return "林晚看了一眼墙上的钟，没有动。"


def test_fake_run_once_calls_six_times_and_cannot_repeat(tmp_path, monkeypatch):
    canary.build(tmp_path)
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    fake = _FakeClient()
    monkeypatch.setattr(canary, "get_llm_client", lambda model: fake)

    report = canary.run_once(tmp_path, authorization_path)
    ledger = _read(tmp_path / "attempt-ledger.json")
    assert len(fake.calls) == 6
    assert report["summary"]["model_calls"] == 6
    assert report["summary"]["subsections"] == 3
    assert report["human_review_pending"] is True
    assert all(item["status"] == "succeeded" for item in ledger["samples"].values())
    assert all(item["attempt_count"] == 1 for item in ledger["samples"].values())
    with pytest.raises(RuntimeError, match="preflight_failed"):
        canary.run_once(tmp_path, authorization_path)
