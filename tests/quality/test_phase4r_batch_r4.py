import json
from pathlib import Path


REPORT = Path("reports/phase4r-batch-r4-attribution.json")
ALLOWED = {
    "missing_scene_spec_fact",
    "ambiguous_scene_spec",
    "incorrect_scene_spec",
    "dropped_context_dependency",
    "writer_instruction_noncompliance",
    "writing_request_boundary_ambiguity",
    "unrelated_generation_variance",
}


def load_report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_r4_is_offline_complete_and_production_unchanged():
    report = load_report()
    assert report["mode"] == "offline_attribution_only"
    assert report["status"] == "completed_stopped"
    assert report["writer_generation_calls"] == 0
    assert report["llm_calls"] == 0
    assert report["production_behavior_changed"] is False
    assert report["production_messages_hash_unchanged"] is True
    assert report["private_generated_prose_emitted"] is False
    assert report["review_provenance"] == "independent_agent_blind_review"


def test_every_blind_review_issue_has_allowed_attribution_and_traceability():
    report = load_report()
    assert report["summary"]["review_label_count"] == 22
    assert report["summary"]["unique_defect_cluster_count"] == 15
    assert set(report["allowed_attributions"]) == ALLOWED
    assert len(report["issues"]) == 22
    for issue in report["issues"]:
        assert issue["attribution"] in ALLOWED
        assert issue["confidence"] in {"low", "medium", "high"}
        assert issue["source_id"] and len(issue["source_hash"]) == 64
        assert issue["source_refs"]
        assert all(ref["source_id"] and len(ref["text_hash"]) == 64 for ref in issue["source_refs"])
        assert issue["defect_evidence"]


def test_scene_spec_execution_findings_are_explicit_and_not_bluntly_blamed_on_context():
    report = load_report()
    issues = report["issues"]
    q4_c = [item for item in issues if item["query_index"] == 4 and item["arm"] == "broker_scene_spec"]
    q8_c = [item for item in issues if item["query_index"] == 8 and item["arm"] == "broker_scene_spec"]
    assert any(item["scene_spec_explicitly_covers_constraint"] and item["writer_violated_explicit_instruction"] for item in q4_c)
    assert any(item["scene_spec_explicitly_covers_constraint"] and item["writer_violated_explicit_instruction"] for item in q8_c)
    assert not any(item["broker_dropped_context_dependency"] for item in issues)
    assert report["decisions"]["token_reduction_directly_caused_q4_or_q8_regression"] == "not_established"
    assert report["decisions"]["next_priority"] == "boundary_validator"


def test_report_keeps_private_messages_and_generated_prose_out_of_git():
    report = load_report()
    raw = REPORT.read_text(encoding="utf-8")
    for forbidden_key in ('"messages"', '"prompt"', '"output_text"', '"candidate_text"'):
        assert forbidden_key not in raw
    assert report["candidate_count"] == 12
    assert report["sample_count"] == 4
    assert report["input_tokens"]["reductions_vs_legacy"]["broker_scene_spec"] >= 0.20


def test_writer_responsibilities_move_non_prose_checks_out_of_writer():
    report = load_report()
    responsibilities = {item["responsibility"]: item for item in report["writer_responsibility_map"]}
    assert responsibilities["prose_generation"]["primary_owner"] == "Writer"
    assert responsibilities["scene_planning"]["primary_owner"] == "Scene Planner"
    assert responsibilities["subsection_boundary_control"]["primary_owner"] == "Validator"
    assert responsibilities["self_check"]["primary_owner"] == "Validator"
    assert responsibilities["self_check"]["secondary_controls"] == ["Repair"]
