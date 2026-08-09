import hashlib
import json

import pytest

from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from experiments.world_runtime_writer_canary import state_commit_canary_wr2c513r5 as canary


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
        "schema_version": "world-runtime-state-commit-canary-c21r4-external-authorization-v1",
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


def test_runner_is_bound_to_chained_components():
    assert canary.EXTRACTOR.name == "semantic_extractor_wr2c513r5.py"
    assert canary.PROJECTOR.name == "semantic_projector_wr2c513r5.py"
    assert canary.VALIDATOR.name == "delta_shadow_wr2c6.py"
    assert canary.ADAPTER.name == "state_commit_adapter_wr2c6.py"
    assert "c21r4" in canary.RUNTIME.name


def test_build_and_preflight_with_authorization(tmp_path):
    manifest = canary.build(tmp_path)
    assert manifest["subsection_count"] == 3
    assert "c21r4" in manifest["experiment_id"]
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    ready = canary.preflight(tmp_path, authorization_path)
    assert ready["ready"] is True
    assert ready["model_calls"] == 6


def _judgments(clock, excerpt, extra=()):
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
    judgments[_TYPES.index("clock_state")] = {
        "change_type": "clock_state",
        "occurred": True,
        "after_value": clock,
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": excerpt, "occurrence": 1}],
    }
    for change_type, after_value, excerpt in extra:
        judgments[_TYPES.index(change_type)] = {
            "change_type": change_type,
            "occurred": True,
            "after_value": after_value,
            "mode": "actual",
            "epistemic": "asserted",
            "evidence": [{"excerpt": excerpt, "occurrence": 1}],
        }
    return judgments


class _ChainedFakeClient:
    def __init__(self):
        self.calls = 0
        self.texts = [
            "六点整，林晚打开店门开始营业。",
            "六点十分，顾客扫码买走一袋面包。",
            "六点二十分，她翻出已打烊的木牌。",
        ]
        self.judgment_sets = [
            _judgments("06:00", "六点整", [("storefront_operation_state", "open", "打开店门开始营业")]),
            _judgments("06:10", "六点十分", [("storefront_public_sale", "occurred", "顾客扫码买走一袋面包")]),
            _judgments("06:20", "六点二十分"),
        ]

    def chat_completion(self, messages, **kwargs):
        self.calls += 1
        kwargs["completion_metadata_sink"]({"latency_seconds": 0.01})
        index = (self.calls - 1) // 2
        if kwargs["json_mode"]:
            return json.dumps({"judgments": self.judgment_sets[index]}, ensure_ascii=False)
        return self.texts[index]


def test_fake_run_once_chained_state_makes_sale_valid(tmp_path, monkeypatch):
    canary.build(tmp_path)
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    fake = _ChainedFakeClient()
    monkeypatch.setattr(canary, "get_llm_client", lambda model: fake)

    report = canary.run_once(tmp_path, authorization_path)
    assert report["summary"]["committed"] == 3
    assert [record["before_revision"] for record in report["records"]] == [7, 8, 9]
    assert [record["after_revision"] for record in report["records"]] == [8, 9, 10]
    s2 = report["records"][1]
    commit = _read(tmp_path / "private/commits/S2.json")
    sale_entries = [
        entry for entry in commit["ledger"]["entries"]
        if entry["change_type"] == "storefront_public_sale"
    ]
    assert s2["accepted"] == 2
    assert len(sale_entries) == 1
    assert sale_entries[0]["after_value"] == "occurred"


def test_fake_run_once_cannot_repeat(tmp_path, monkeypatch):
    canary.build(tmp_path)
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    fake = _ChainedFakeClient()
    monkeypatch.setattr(canary, "get_llm_client", lambda model: fake)
    canary.run_once(tmp_path, authorization_path)
    with pytest.raises(RuntimeError, match="preflight_failed"):
        canary.run_once(tmp_path, authorization_path)
