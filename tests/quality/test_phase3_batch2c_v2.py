from tests.benchmarks.benchmark_phase3_v2 import _review_has_work
from tests.quality.baseline import ROOT, load_json


REPORT_PATH = ROOT / "reports" / "phase3-batch2c-v2-shadow.json"
DIFF_PATH = ROOT / "tests" / "quality" / "phase3_batch2c_new_candidates_review.json"


def test_batch2c_report_is_real_shadow_and_preserves_frozen_systems():
    report = load_json(REPORT_PATH)
    assert report["mode"] == "real_v1_v2_shadow_comparison"
    assert report["production_changed"] is False
    assert report["writer_changed"] is False
    assert report["default_configuration_changed"] is False
    assert report["collection_strategy"] == "shared_collection_task_id_filter"
    assert report["v2_profile"]["max_queries"] == 2
    assert report["v2_profile"]["token_budget"] == 600


def test_batch2c_v1_v2_metrics_keep_proxy_and_reviewed_metrics_separate():
    report = load_json(REPORT_PATH)
    v1 = report["v1"]["metrics"]
    v2 = report["v2"]["metrics"]

    assert v1["selected_candidates"] == 38
    assert v1["reviewed_closed_set_precision"] == 0.5526
    assert v1["pooled_known_relevant_retention"] == 0.913
    assert v1["gold_section_candidate_pool_coverage"] == 0.7667
    assert v2["selected_candidates"] == 28
    assert v2["known_relevant_selected"] == 10
    assert v2["known_irrelevant_selected"] == 6
    assert v2["unlabeled_selected"] == 12
    assert v2["reviewed_closed_set_precision"] == 0.625
    assert v2["pooled_known_relevant_retention"] == 0.4348
    assert v2["late_reviewed_closed_set_precision"] == 0.2
    assert v2["gold_section_candidate_pool_coverage"] == 0.8
    assert v2["mean_token_estimate"] < v1["mean_token_estimate"]
    assert all(item["token_estimate"] <= 600 for item in v2["per_query"])


def test_batch2c_v2_traces_have_graded_character_evidence_and_two_queries_max():
    report = load_json(REPORT_PATH)

    for run in report["v2"]["queries"]:
        assert len(run["plan"]["queries"]) <= 2
        selected = [item for item in run["candidate_trace"] if item["selected"]]
        assert all(item["character_evidence"]["mode"] == "graded" for item in selected)
        assert all(item["reason"].startswith("selected:") for item in selected)
        assert all(item["estimated_tokens"] > 0 for item in selected)


def test_batch2c_does_not_request_review_when_unknowns_cannot_change_decision():
    report = load_json(REPORT_PATH)
    review = load_json(DIFF_PATH)

    assert report["optimistic_unknown_upper_bound"] == {
        "assumption": "every V2 unlabeled selection is relevant",
        "reviewed_relevant_selected": 10,
        "unlabeled_selected": 12,
        "pooled_relevant_total_if_all_unknown_relevant": 35,
        "maximum_pooled_known_relevant_retention": 0.6286,
        "can_reach_0_90_retention_gate": False,
    }
    assert review["summary"]["candidate_count"] == 0
    assert review["summary"]["suppressed_new_top5_candidates"] == 12
    assert review["summary"]["status"] == "review_not_required_release_gate_already_impossible"
    assert review["candidates"] == []
    assert _review_has_work(DIFF_PATH) is False
    assert report["all_release_gates_passed"] is False
    assert report["decision"] == "remain_shadow"
