from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.writer_boundary_v12_r35.builder import (
    LLM_CLIENT_SOURCE,
    R341_AGGREGATE,
    R34_MANIFEST,
    R34_PROBE_PLAN,
    activation_gate,
    build,
    build_probe_envelope,
    validate_inputs,
)
from experiments.writer_boundary_v12_r35.controller import (
    CapabilityProbeDisabledError,
    ProbeActivationController,
)
from experiments.writer_boundary_v12_r35.models import ActivationGate


def test_inputs_are_pinned_and_review_is_unanimous_advisory():
    validate_inputs()
    aggregate = json.loads(R341_AGGREGATE.read_text(encoding="utf-8"))
    assert aggregate["recommendation_tally"]["recommend_layer_build"] == 3
    assert aggregate["authorization"]["capability_probe_layer_build_authorized"] is False
    assert aggregate["authorization"]["model_call_authorized"] is False


@pytest.mark.parametrize(
    "field",
    [
        "capability_probe_call_authorized",
        "probe_execution_enabled",
        "real_generation_authorized",
        "real_generation_enabled",
        "model_call_authorized",
        "provider_client_creation_authorized",
        "network_access_authorized",
    ],
)
def test_gate_rejects_every_runtime_authority(field: str):
    raw = activation_gate().model_dump(mode="json")
    raw[field] = True
    with pytest.raises(ValidationError):
        ActivationGate.model_validate(raw)


def test_gate_rejects_positive_probe_quota():
    raw = activation_gate().model_dump(mode="json")
    raw["probe_request_quota"] = 1
    with pytest.raises(ValidationError):
        ActivationGate.model_validate(raw)


def test_controller_dry_runs_but_never_arms_or_executes():
    gate = activation_gate()
    envelope = build_probe_envelope(gate)
    controller = ProbeActivationController(gate)
    receipt = controller.dry_run(envelope)
    assert receipt.provider_client_created is False
    assert receipt.provider_request_sent is False
    assert receipt.attempt_count == 0
    with pytest.raises(CapabilityProbeDisabledError):
        controller.arm()
    with pytest.raises(CapabilityProbeDisabledError):
        controller.execute_probe(envelope)


def test_probe_envelope_tamper_is_rejected():
    gate = activation_gate()
    envelope = build_probe_envelope(gate)
    changed = envelope.model_copy(
        update={
            "messages": [
                {"role": "system", "content": "changed"},
                {"role": "user", "content": "changed"},
            ]
        }
    )
    with pytest.raises(ValueError):
        ProbeActivationController(gate).dry_run(changed)


def test_full_build_has_zero_calls_attempts_and_responses(tmp_path: Path):
    output = tmp_path / "output"
    audit = build(output, tmp_path / "report.md")
    assert audit["r3_5_static_pass"] is True
    assert audit["provider_client_created"] is False
    assert audit["provider_requests_sent"] == 0
    assert audit["probe_responses"] == 0
    assert audit["model_calls"] == audit["fiction_texts"] == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["activation_layer_build_authorized"] is True
    assert manifest["capability_probe_call_authorized"] is False
    assert manifest["probe_request_quota"] == 0


def test_probe_ledger_is_empty_of_attempts(tmp_path: Path):
    output = tmp_path / "output"
    build(output, tmp_path / "report.md")
    with sqlite3.connect(output / "private/probe-ledger.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM probe_plan").fetchone()[0] == 1
        assert db.execute("SELECT request_quota FROM probe_plan").fetchone()[0] == 0
        assert db.execute("SELECT attempt_count FROM probe_plan").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM probe_attempts").fetchone()[0] == 0


def test_build_preserves_all_pinned_sources(tmp_path: Path):
    sources = (R34_MANIFEST, R34_PROBE_PLAN, R341_AGGREGATE, LLM_CLIENT_SOURCE)
    before = {path: path.read_bytes() for path in sources}
    build(tmp_path / "output", tmp_path / "report.md")
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_r35_source_has_no_provider_or_network_client_call():
    root = Path(__file__).resolve().parents[2]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "experiments/writer_boundary_v12_r35/controller.py",
            "experiments/writer_boundary_v12_r35/builder.py",
        )
    ).lower()
    assert "get_llm_client" not in source
    assert "chat_completion(" not in source
    assert "from openai" not in source
    assert "import requests" not in source
    assert "import httpx" not in source
    assert "import socket" not in source
