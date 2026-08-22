import inspect
import json
from pathlib import Path

from app import coordinator
from app.agents.writer import Writer
from app.writing.generation_controller import GenerationController


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "writer-condense-canary-integration.json"


def test_report_captures_fixed_cost_and_canary_limits():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    baseline = report["baseline"]
    assert baseline["target_words_by_subsection"] == [1000] * 4
    assert baseline["raw_characters_by_subsection"] == [1235, 2377, 1934, 1498]
    assert baseline["final_characters_by_subsection"] == [1235, 2133, 1591, 1226]
    assert baseline["total_removed_characters"] == 859
    assert baseline["total_condense_known_tokens"] == 8998
    assert baseline["total_condense_latency_seconds"] == 46.1
    assert baseline["same_shape_warn_expected_http_post_count"] == 19
    assert report["configuration"]["default"] == "warn"
    assert report["real_demo"]["status"] == "passed"
    assert report["real_demo"]["task_id"] == "4ce7e82f-d3b4-44a6-8dcf-ec1e638d77e9"
    assert report["real_demo"]["http_post_count"] == 19
    assert report["real_demo"]["condense_started_count"] == 0
    assert report["real_demo"]["retained_original_count"] == 4
    assert report["real_demo"]["mandatory_event_retry_count"] == 0
    assert report["real_demo"]["user_usability_judgment"] == "usable"
    assert report["real_demo"]["production_default_promotion_authorized"] is True
    assert report["scope"]["writer_llm_calls"] == 0


def test_handover_order_is_recorded_but_not_changed():
    source = inspect.getsource(Writer.run)
    assert source.index("self._extract_handover_with_observation(") < source.index(
        "self._adjust_generated_length("
    )
    assert source.index("commit_handover_effects") < source.index(
        "self._adjust_generated_length("
    )


def test_final_reviews_and_writer_boundary_remain_independent():
    review_source = inspect.getsource(coordinator._phase_review)
    controller_source = inspect.getsource(GenerationController.adjust_length)
    assert "reviewer.review_section" in review_source
    assert "reviewer.review_global" in review_source
    assert "WRITER_CONDENSE_MODE" not in review_source
    assert "from tests" not in controller_source
    assert "import tests" not in controller_source


def test_public_report_contains_no_private_payload_fields():
    serialized = REPORT.read_text(encoding="utf-8").lower()
    for forbidden_key in ('"prompt"', '"messages"', '"full_text"', '"api_key"'):
        assert forbidden_key not in serialized
