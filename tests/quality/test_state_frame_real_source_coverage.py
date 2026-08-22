import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "benchmarks" / "audit_state_frame_real_sources.py"
REPORT = ROOT / "reports" / "state-frame-batch2-real-source-coverage.json"


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_audit_has_no_evaluation_or_generated_output_dependency():
    source = SCRIPT.read_text(encoding="utf-8")
    forbidden = (
        "candidate_", "arm_mapping.private", "user_review.completed",
        "evaluation.private", "must_recall_facts", "gold_sections",
        "blind_review", "human_relevant",
    )
    assert all(value not in source for value in forbidden)
    imports = "\n".join(
        ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
    )
    assert "redis" not in imports.lower()
    assert "sqlite" not in imports.lower()
    assert "chroma" not in imports.lower()
    assert "llm" not in imports.lower()
    assert "writer" not in imports.lower()


def test_public_report_is_real_read_only_and_private_text_free():
    report = _report()
    assert report["mode"] == "real_frozen_source_coverage_audit"
    assert report["scene_count"] == 4
    assert report["writer_generation_calls"] == 0
    assert report["llm_calls"] == 0
    assert report["redis_writes"] == 0
    assert report["sqlite_writes"] == 0
    assert report["chroma_writes"] == 0
    assert report["runtime_evaluation_fields_used"] == []
    assert report["production_messages_changed"] is False
    assert report["contains_story_text"] is False
    rendered = json.dumps(report, ensure_ascii=False)
    for forbidden in ("完整正文", "Writer messages", "API key", "character_state\": \""):
        assert forbidden not in rendered


def test_real_frames_preserve_status_traceability_and_exclusions():
    report = _report()
    checks = report["mechanical_checks"]
    assert checks["all_sources_traceable"] is True
    assert checks["unknown_conflicted_retention_100"] is True
    assert checks["planned_hard_intrusions_zero"] is True
    assert checks["duplicate_classification_zero"] is True
    assert checks["frame_hash_deterministic"] is True
    assert checks["keyword_inference_not_used"] is True
    assert all(item["status_preserved"] for item in report["scenes"])
    assert all(not item["contains_story_text"] for item in report["scenes"])


def test_diagnosis_is_one_of_the_frozen_contract_outcomes():
    report = _report()
    assert report["diagnosis"] in {
        "ready_for_composition_contract",
        "upstream_state_contract_required",
        "insufficient_real_source_data",
    }
    if report["diagnosis"] == "upstream_state_contract_required":
        assert report["minimum_upstream_contract"] == [
            "state_id", "predicate", "subject", "value", "epistemic_status",
            "effective_from", "effective_until", "section", "subsection",
            "source_id", "text_hash",
        ]
