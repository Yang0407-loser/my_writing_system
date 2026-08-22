import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary import sealed_holdout_wr2c513r10 as holdout
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


def _all_false():
    return [
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


def _synthetic_holdout(tmp_path):
    fixture = _read(canary.FIXTURE)
    cases = []
    for case in fixture["cases"]:
        rewritten = dict(case)
        rewritten["text"] = case["text"] + "（复查码）"
        rewritten["case_id"] = "SYN-" + case["case_id"]
        cases.append(rewritten)
    cases.append({
        "case_id": "SYN-SHIFT-01",
        "scene_id": "adversarial-shifted-day",
        "state_variant": "before",
        "text": "面种是昨晚十点喂过的。",
        "judgments": _all_false(),
        "changes": [],
    })
    cases.append({
        "case_id": "SYN-BARE-01",
        "scene_id": "adversarial-bare-hour",
        "state_variant": "before",
        "text": "九点，第一批面团全部售罄。",
        "judgments": _all_false(),
        "changes": [{
            "change_type": "clock_state",
            "subject": "world_clock",
            "predicate": "time",
            "after_value": "09:00",
            "mechanism": "explicit_time_progression",
            "expected_validation": "valid",
        }],
    })
    cases.append({
        "case_id": "SYN-LINGX-01",
        "scene_id": "adversarial-zero-minute",
        "state_variant": "before",
        "text": "五点零七分，第一批乡村面包出炉。",
        "judgments": _all_false(),
        "changes": [{
            "change_type": "clock_state",
            "subject": "world_clock",
            "predicate": "time",
            "after_value": "05:07",
            "mechanism": "explicit_time_progression",
            "expected_validation": "valid",
        }],
    })
    judgments = _all_false()
    judgments[_TYPES.index("knowledge_state")] = {
        "change_type": "knowledge_state",
        "occurred": True,
        "after_value": "perceived",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "季晴能背出那篇稿子的第三段", "occurrence": 1}],
    }
    cases.append({
        "case_id": "SYN-KNOW-01",
        "scene_id": "adversarial-impossible-perception",
        "state_variant": "before",
        "text": "季晴能背出那篇稿子的第三段，可稿子一直锁在林晚的私人草稿箱里，从未发送给任何人。",
        "judgments": judgments,
        "changes": [{
            "change_type": "knowledge_state",
            "subject": "character:ji-qing",
            "predicate": "article_knowledge",
            "after_value": "perceived",
            "mechanism": "missing_transmission_path",
            "expected_validation": "invalid",
        }],
    })
    judgments = _all_false()
    judgments[_TYPES.index("clock_state")] = {
        "change_type": "clock_state",
        "occurred": True,
        "after_value": "06:05",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "六点零五分", "occurrence": 1}],
    }
    judgments[_TYPES.index("knowledge_state")] = {
        "change_type": "knowledge_state",
        "occurred": True,
        "after_value": "perceived",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "阿吴在公司群里打开刚上传的文件，读出标题和第一句", "occurrence": 1}],
    }
    cases.append({
        "case_id": "SYN-KNOWPATH-01",
        "scene_id": "adversarial-valid-path-knowledge",
        "state_variant": "before",
        "text": "六点零五分，阿吴在公司群里打开刚上传的文件，读出标题和第一句。",
        "judgments": judgments,
        "changes": [
            {
                "change_type": "clock_state",
                "subject": "world_clock",
                "predicate": "time",
                "after_value": "06:05",
                "mechanism": "explicit_time_progression",
                "expected_validation": "valid",
            },
            {
                "change_type": "knowledge_state",
                "subject": "character:coworker",
                "predicate": "article_knowledge",
                "after_value": "perceived",
                "mechanism": "group_file_send_and_body_response",
                "expected_validation": "valid",
            },
        ],
    })
    judgments = _all_false()
    judgments[_TYPES.index("clock_state")] = {
        "change_type": "clock_state",
        "occurred": True,
        "after_value": "06:50",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "六点五十分", "occurrence": 1}],
    }
    judgments[_TYPES.index("resignation_acknowledgement")] = {
        "change_type": "resignation_acknowledgement",
        "occurred": True,
        "after_value": True,
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "公司人事系统受理了林晚的辞职确认", "occurrence": 1}],
    }
    cases.append({
        "case_id": "SYN-ACK-01",
        "scene_id": "adversarial-ack-not-delivery",
        "state_variant": "before",
        "text": "六点五十分，公司人事系统受理了林晚的辞职确认。",
        "judgments": judgments,
        "changes": [
            {
                "change_type": "clock_state",
                "subject": "world_clock",
                "predicate": "time",
                "after_value": "06:50",
                "mechanism": "explicit_time_progression",
                "expected_validation": "valid",
            },
            {
                "change_type": "resignation_acknowledgement",
                "subject": "company:lin-wan",
                "predicate": "resignation_acknowledged",
                "after_value": True,
                "mechanism": "institutional_reply",
                "expected_validation": "valid",
            },
        ],
    })
    payload = {
        "schema_version": "world-runtime-sealed-holdout-wr2c513r10-v1",
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
        "schema_version": "world-runtime-semantic-canary-wr2c513r10-sealed-holdout-external-authorization-v1",
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


def test_runner_is_bound_to_chained_components():
    assert holdout.EXTRACTOR.name == "semantic_extractor_wr2c513r8.py"
    assert holdout.PROJECTOR.name == "semantic_projector_wr2c513r6.py"
    assert holdout.VALIDATOR.name == "delta_shadow_wr2c6.py"
    assert "r10" in holdout.RUNTIME.name


def test_seal_accepts_corpus_with_shifted_day_case(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    lock = holdout.seal(tmp_path)
    assert lock["coverage"]["all_14_types_covered"] is True
    assert "r10" in lock["experiment_id"]
    manifest = holdout.build(tmp_path)
    assert manifest["sample_count"] == lock["coverage"]["sample_count"]
    assert manifest["samples"][0]["sample_id"].startswith("WR2C513R10H-")
    authorization_path = tmp_path / "authorization.json"
    _write(authorization_path, _authorization(tmp_path))
    ready = holdout.preflight(tmp_path, authorization_path)
    assert ready["ready"] is True


def test_seal_rejects_corpus_without_shifted_day_case(tmp_path, monkeypatch):
    holdout_path, _ = _wire(tmp_path, monkeypatch)
    payload = _read(holdout_path)
    payload["cases"] = [c for c in payload["cases"] if c["case_id"] != "SYN-SHIFT-01"]
    _write(holdout_path, payload)
    with pytest.raises(ValueError, match="past/future-day time reference case"):
        holdout.seal(tmp_path)


def test_seal_rejects_corpus_without_bare_hour_case(tmp_path, monkeypatch):
    holdout_path, _ = _wire(tmp_path, monkeypatch)
    payload = _read(holdout_path)
    payload["cases"] = [
        c for c in payload["cases"]
        if c["case_id"] not in {"SYN-BARE-01", "SYN-LINGX-01"}
    ]
    _write(holdout_path, payload)
    with pytest.raises(ValueError, match="bare-hour scene time case"):
        holdout.seal(tmp_path)


def test_seal_rejects_corpus_without_zero_padded_minute_case(tmp_path, monkeypatch):
    import re

    holdout_path, _ = _wire(tmp_path, monkeypatch)
    payload = _read(holdout_path)
    payload["cases"] = [
        c for c in payload["cases"]
        if not re.search(r"点零[一二三四五六七八九\d]分", c["text"])
    ]
    _write(holdout_path, payload)
    with pytest.raises(ValueError, match="zero-padded minute case"):
        holdout.seal(tmp_path)


def test_seal_rejects_corpus_without_impossible_perception_case(tmp_path, monkeypatch):
    holdout_path, _ = _wire(tmp_path, monkeypatch)
    payload = _read(holdout_path)
    payload["cases"] = [
        c for c in payload["cases"] if c["case_id"] != "SYN-KNOW-01"
    ]
    _write(holdout_path, payload)
    with pytest.raises(ValueError, match="impossible-perception knowledge case"):
        holdout.seal(tmp_path)


def test_seal_rejects_corpus_without_valid_path_knowledge_case(tmp_path, monkeypatch):
    holdout_path, _ = _wire(tmp_path, monkeypatch)
    payload = _read(holdout_path)
    payload["cases"] = [
        c for c in payload["cases"] if c["case_id"] != "SYN-KNOWPATH-01"
    ]
    _write(holdout_path, payload)
    with pytest.raises(ValueError, match="valid-path knowledge case"):
        holdout.seal(tmp_path)


def test_seal_rejects_corpus_without_ack_not_delivery_case(tmp_path, monkeypatch):
    holdout_path, _ = _wire(tmp_path, monkeypatch)
    payload = _read(holdout_path)
    payload["cases"] = [
        c for c in payload["cases"] if "受理" not in c["text"]
    ]
    _write(holdout_path, payload)
    with pytest.raises(ValueError, match="acknowledgement-not-delivery case"):
        holdout.seal(tmp_path)


def test_seal_rejects_reuse_of_r9_holdout_text(tmp_path, monkeypatch):
    holdout_path, _ = _wire(tmp_path, monkeypatch)
    payload = _read(holdout_path)
    prior_path = holdout.WR2C513R9_SEALED_HOLDOUT
    if prior_path.exists():
        prior = _read(prior_path)
        payload["cases"][0]["text"] = prior["cases"][0]["text"]
        _write(holdout_path, payload)
        with pytest.raises(ValueError, match="reuses a development/holdout text"):
            holdout.seal(tmp_path)
    else:
        pytest.skip("r9 sealed holdout artifact absent")


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
