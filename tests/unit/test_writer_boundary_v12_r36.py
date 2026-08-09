from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.writer_boundary_v12_r36.builder import (
    R35_ACTIVATION_GATE,
    R35_AGGREGATE,
    R35_ENVELOPE,
    R35_MANIFEST,
    build,
    call_gate,
    validate_inputs,
)
from experiments.writer_boundary_v12_r36.executor import (
    ProbeQuotaConsumedError,
    execute_once,
)
from experiments.writer_boundary_v12_r36.models import SingleProbeCallGate


def test_inputs_are_pinned_and_authorization_is_exactly_one_probe():
    config, envelope = validate_inputs()
    assert envelope.probe_id == "CAPABILITY-PROBE-R35-001"
    assert config["provider_request_quota"] == 1
    assert config["transport_max_retries"] == 0
    assert config["general_model_calls_authorized"] is False
    assert config["real_generation_authorized"] is False
    assert config["fiction_generation_authorized"] is False


@pytest.mark.parametrize(
    "field",
    [
        "silent_retry_authorized",
        "reserve_run_authorized",
        "general_model_calls_authorized",
        "real_generation_authorized",
        "real_generation_enabled",
        "fiction_generation_authorized",
        "production_integration_authorized",
    ],
)
def test_gate_rejects_every_scope_expansion(field: str):
    _, envelope = validate_inputs()
    raw = call_gate(envelope).model_dump(mode="json")
    raw[field] = True
    with pytest.raises(ValidationError):
        SingleProbeCallGate.model_validate(raw)


def test_gate_rejects_quota_above_one_and_transport_retry():
    _, envelope = validate_inputs()
    raw = call_gate(envelope).model_dump(mode="json")
    raw["provider_request_quota"] = 2
    with pytest.raises(ValidationError):
        SingleProbeCallGate.model_validate(raw)
    raw = call_gate(envelope).model_dump(mode="json")
    raw["transport_max_retries"] = 1
    with pytest.raises(ValidationError):
        SingleProbeCallGate.model_validate(raw)


def test_successful_call_occurs_once_and_consumes_quota(tmp_path: Path):
    output = tmp_path / "output"
    build(output, tmp_path / "report.md")
    calls = []

    def fake_call(envelope):
        calls.append(envelope.probe_id)
        return "CAPABILITY_OK", {
            "finish_reason": "stop",
            "input_tokens": 20,
            "output_tokens": 2,
            "latency_seconds": 0.1,
        }

    receipt = execute_once(output, fake_call)
    assert receipt.outcome == "succeeded"
    assert receipt.response_exactly_expected is True
    assert calls == ["CAPABILITY-PROBE-R35-001"]
    with pytest.raises(ProbeQuotaConsumedError):
        execute_once(output, fake_call)
    assert calls == ["CAPABILITY-PROBE-R35-001"]
    with sqlite3.connect(output / "private/single-probe-ledger.sqlite") as db:
        assert db.execute("SELECT quota_remaining FROM probe_state").fetchone()[0] == 0
        assert db.execute("SELECT attempt_count FROM probe_state").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM probe_attempts").fetchone()[0] == 1


def test_failed_call_is_not_retried_and_quota_remains_consumed(tmp_path: Path):
    output = tmp_path / "output"
    build(output, tmp_path / "report.md")
    calls = []

    def failing_call(envelope):
        calls.append(envelope.probe_id)
        raise RuntimeError("synthetic provider failure")

    receipt = execute_once(output, failing_call)
    assert receipt.outcome == "failed"
    assert receipt.provider_request_attempt_count == 1
    assert calls == ["CAPABILITY-PROBE-R35-001"]
    with pytest.raises(ProbeQuotaConsumedError):
        execute_once(output, failing_call)
    assert calls == ["CAPABILITY-PROBE-R35-001"]


def test_build_refuses_to_reset_existing_ledger(tmp_path: Path):
    output = tmp_path / "output"
    build(output, tmp_path / "report.md")
    with pytest.raises(FileExistsError):
        build(output, tmp_path / "report.md")


def test_execution_outputs_never_enable_real_or_fiction_generation(tmp_path: Path):
    output = tmp_path / "output"
    build(output, tmp_path / "report.md")

    def fake_call(envelope):
        return "CAPABILITY_OK", {}

    execute_once(output, fake_call)
    audit = json.loads(
        (output / "r3-6-post-execution-audit.json").read_text(encoding="utf-8")
    )
    assert audit["general_model_calls"] == 0
    assert audit["real_generations"] == 0
    assert audit["fiction_texts"] == 0
    assert audit["second_call_authorized"] is False


def test_build_preserves_all_pinned_r35_inputs(tmp_path: Path):
    sources = (R35_MANIFEST, R35_ACTIVATION_GATE, R35_AGGREGATE, R35_ENVELOPE)
    before = {path: path.read_bytes() for path in sources}
    build(tmp_path / "output", tmp_path / "report.md")
    assert all(path.read_bytes() == raw for path, raw in before.items())
