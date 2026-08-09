from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from experiments.writer_boundary_v12_r34.adapter import (
    DisabledDeepSeekAdapter,
    GenerationDisabledError,
    provider_call_spec,
)
from experiments.writer_boundary_v12_r34.builder import (
    LLM_CLIENT_SOURCE,
    R331_APPROVAL,
    R3_REQUESTS,
    build,
    build_queue,
    validate_inputs,
)
from experiments.writer_boundary_v12_r34.models import GenerationGate


def test_r34_source_pins_are_repository_fixtures():
    fixture_root = (
        Path(__file__).resolve().parents[2]
        / "experiments/writer_boundary_v12_shared/fixtures"
    )
    for source in (R331_APPROVAL, R3_REQUESTS, LLM_CLIENT_SOURCE):
        assert source.is_relative_to(fixture_root)
        assert source.is_file()


def gate() -> GenerationGate:
    return GenerationGate(
        schema_version="1.2-r3.4-generation-gate",
        package_build_authorized=True,
        capability_probe_authorized=False,
        real_generation_authorized=False,
        model_call_authorized=False,
        generation_enabled=False,
    )


def test_r34_inputs_are_pinned_and_approval_is_build_only():
    _, requests = validate_inputs()
    assert len(requests) == 36
    approval = json.loads(R331_APPROVAL.read_text(encoding="utf-8"))
    assert approval["aggregate_verdict"] == "authorize_generation_package_build_only"
    assert approval["authorization"]["real_generation_authorized"] is False
    assert approval["authorization"]["model_call_authorized"] is False


def test_queue_binds_all_36_locked_requests_and_call_spec():
    _, requests = validate_inputs()
    queue = build_queue(requests, gate())
    assert len(queue) == 36
    assert [item.ordinal for item in queue] == list(range(1, 37))
    assert {item.arm for item in queue} == {"A", "B", "C"}
    assert len({item.source_request_sha256 for item in queue}) == 36
    assert len({item.execution_envelope_sha256 for item in queue}) == 36
    assert all(item.call_spec == provider_call_spec() for item in queue)


def test_provider_spec_freezes_thinking_json_seed_and_retries():
    spec = provider_call_spec()
    assert spec.thinking == "disabled"
    assert spec.json_mode is False
    assert spec.seed is None
    assert spec.seed_required is False
    assert spec.seed_capability == "unverified_dependency"
    assert spec.transport_max_retries == 0


def test_disabled_adapter_dry_runs_but_never_executes():
    _, requests = validate_inputs()
    item = build_queue(requests, gate())[0]
    adapter = DisabledDeepSeekAdapter(gate())
    receipt = adapter.dry_run(item)
    assert receipt.provider_request_sent is False
    assert receipt.attempt_count == 0
    with pytest.raises(GenerationDisabledError):
        adapter.execute(item)
    changed = item.model_copy(update={"messages": [{"role": "user", "content": "changed"}]})
    with pytest.raises(ValueError):
        adapter.dry_run(changed)


def test_full_package_is_disabled_and_contains_no_attempts(tmp_path: Path):
    output = tmp_path / "output"
    result = build(output, tmp_path / "report.md")
    assert result["r3_4_static_pass"] is True
    assert result["queue_items"] == result["dry_run_receipts"] == 36
    assert result["provider_requests_sent"] == 0
    assert result["retry_attempt_rows"] == 0
    assert result["generation_enabled"] is False
    assert result["model_calls"] == result["fiction_texts"] == 0
    with sqlite3.connect(output / "private/retry-ledger.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 36
        assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_capability_probe_is_plan_only_and_not_authorized(tmp_path: Path):
    output = tmp_path / "output"
    build(output, tmp_path / "report.md")
    plan = json.loads((output / "capability-probe-plan.json").read_text(encoding="utf-8"))
    assert plan["status"] == "not_run_not_authorized"
    assert plan["provider_request_sent"] is False
    assert plan["model_calls"] == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["capability_probe_authorized"] is False
    assert manifest["real_generation_authorized"] is False
    assert manifest["model_call_authorized"] is False


def test_outputs_contain_no_api_key_material(tmp_path: Path):
    output = tmp_path / "output"
    build(output, tmp_path / "report.md")
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in output.rglob("*")
        if path.is_file() and path.suffix != ".sqlite"
    ).lower()
    assert "authorization: bearer" not in text
    assert "sk-" not in text
    assert "llm_api_key" not in text


def test_build_preserves_pinned_sources(tmp_path: Path):
    before = {
        path: path.read_bytes()
        for path in (R331_APPROVAL, R3_REQUESTS, LLM_CLIENT_SOURCE)
    }
    build(tmp_path / "output", tmp_path / "report.md")
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_r34_source_has_no_network_or_client_call():
    root = Path(__file__).resolve().parents[2]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "experiments/writer_boundary_v12_r34/adapter.py",
            "experiments/writer_boundary_v12_r34/builder.py",
        )
    )
    assert "get_llm_client" not in source
    assert "chat_completion(" not in source
    assert "openai" not in source.lower()
