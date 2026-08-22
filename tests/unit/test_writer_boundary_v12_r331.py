from __future__ import annotations

import json
from pathlib import Path

from experiments.writer_boundary_v12_r3.kernel import digest_bytes
from experiments.writer_boundary_v12_r331.builder import R33_HISTORY, build


def test_r331_delivery_bytes_match_manifests_on_disk(tmp_path: Path):
    output = tmp_path / "output"
    result = build(output, tmp_path / "report.md")
    assert result["r3_3_1_static_pass"] is True
    assert result["delivery_count"] == 4
    assert result["all_on_disk_hashes_match"] is True
    for manifest_path in output.glob("deliveries/*/manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        required = manifest["required_files"][0]
        package = output / required["path"]
        raw = package.read_bytes()
        assert digest_bytes(raw) == required["sha256"]
        assert len(raw) == required["bytes"]


def test_r331_packages_use_lf_exact_bytes(tmp_path: Path):
    output = tmp_path / "output"
    build(output, tmp_path / "report.md")
    for package in output.glob("deliveries/*/package.json"):
        raw = package.read_bytes()
        assert b"\r\n" not in raw
        assert raw.endswith(b"\n")


def test_r331_runbook_requires_kernel_validation(tmp_path: Path):
    output = tmp_path / "output"
    build(output, tmp_path / "report.md")
    runbook = json.loads(
        (output / "review-acceptance-runbook.json").read_text(encoding="utf-8")
    )
    assert any("validate_audits" in item for item in runbook["execution_audit_acceptance"])
    assert any("validate_votes" in item for item in runbook["preference_vote_acceptance"])
    assert runbook["model_call_authorized"] is False


def test_r331_preserves_r33_history(tmp_path: Path):
    before = {str(path): digest_bytes(path.read_bytes()) for path in R33_HISTORY}
    build(tmp_path / "output", tmp_path / "report.md")
    after = {str(path): digest_bytes(path.read_bytes()) for path in R33_HISTORY}
    assert before == after


def test_r331_is_zero_call_and_does_not_authorize_generation(tmp_path: Path):
    output = tmp_path / "output"
    result = build(output, tmp_path / "report.md")
    assert result["model_calls"] == result["fiction_texts"] == 0
    assert result["generation_authorized"] is False
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation_package_build_authorized"] is False
    assert manifest["model_call_authorized"] is False


def test_r331_source_has_no_model_client():
    root = Path(__file__).resolve().parents[2]
    source = (root / "experiments/writer_boundary_v12_r331/builder.py").read_text(
        encoding="utf-8"
    )
    assert "get_llm_client" not in source
    assert "chat_completion" not in source
