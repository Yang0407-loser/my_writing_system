import inspect
import json
from pathlib import Path

from app import coordinator
from app.agents.writer import Writer


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "reports" / "writer-incremental-section-review-dedup.json"


def test_incremental_guard_does_not_cover_experience_extraction_or_commit():
    run_source = inspect.getsource(Writer.run)
    guard_source = inspect.getsource(Writer._maybe_start_incremental_section_review)

    assert "_run_experience_extraction" in run_source
    assert "extract_from_section" in run_source
    assert "threading.Thread(target=_run_experience_extraction" in run_source
    assert "commit_subsection" in run_source
    assert "extract_from_section" not in guard_source
    assert "commit_subsection" not in guard_source


def test_final_review_is_independent_of_incremental_configuration():
    source = inspect.getsource(coordinator._phase_review)
    assert "reviewer.review_section" in source
    assert "reviewer.review_global" in source
    assert "WRITER_INCREMENTAL_SECTION_REVIEW" not in source


def test_production_app_does_not_import_tests():
    for target in (Writer, coordinator._phase_review):
        assert "from tests" not in inspect.getsource(target)
        assert "import tests" not in inspect.getsource(target)


def test_report_keeps_review_and_experience_chains_separate():
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    audit = report["consumer_audit"]
    assert audit["incremental_writer_review"]["controls_final_review"] is False
    assert audit["final_section_review"]["preserved"] is True
    assert audit["global_review"]["preserved"] is True
    assert audit["experience_extraction"]["preserved"] is True
    assert report["baseline_cost"] == {
        "http_post_count": 25,
        "incremental_review_calls": 2,
        "incremental_review_known_tokens": 7495,
        "incremental_review_latency_seconds": 15.1,
        "same_shape_expected_http_post_count": 23,
        "logged_total_includes_incremental_review": False,
        "official_total_not_a_valid_acceptance_metric": True,
    }
    demo = report["real_demo"]
    assert demo["status"] == "passed"
    assert demo["http_post_count"] == 22
    assert demo["http_count_comparison"]["reconciled_http_posts"] == 22
    assert demo["http_count_comparison"]["removed_incremental_review_calls"] == 2
    assert demo["main_draft_calls"] == demo["subsections"] == 4
    assert demo["mandatory_event_actual_retries"] == 0
    assert demo["incremental_review_started_calls"] == 0
    assert demo["experience_extraction_calls"] == 1
    assert demo["final_section_review_calls"] == 1
    assert demo["global_review_calls"] == 1
    assert demo["task_status"] == demo["checkpoint_phase"] == "completed"
    assert demo["acceptance"]["incremental_review_elimination_confirmed"] is True
    assert demo["acceptance"]["exact_same_input_token_saving_measured"] is False
    assert report["scope"]["writer_llm_calls"] == 0
    assert report["next_optimization_started"] is False


def test_public_report_contains_no_private_payload_fields():
    serialized = REPORT_PATH.read_text(encoding="utf-8").lower()
    for forbidden_key in ('"prompt"', '"messages"', '"full_text"', '"api_key"'):
        assert forbidden_key not in serialized
