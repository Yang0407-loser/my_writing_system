from tests.benchmarks.benchmark_phase3_2d import _review_has_work
from tests.quality.baseline import ROOT, load_json


REPORT_PATH = ROOT / "reports" / "phase3-batch2d-loss-attribution.json"
REVIEW_PATH = ROOT / "tests" / "quality" / "phase3_batch2d_decision_candidates_review.json"


def test_batch2d_report_freezes_real_2x2_metrics_and_invariants():
    report = load_json(REPORT_PATH)
    combinations = report["combinations"]

    assert report["mode"] == "real_2x2_shadow_loss_attribution"
    assert report["production_changed"] is False
    assert report["writer_changed"] is False
    assert report["default_configuration_changed"] is False
    assert report["collection_strategy"] == "shared_collection_task_id_filter"
    assert set(combinations) == {"p1_r1", "p1_r2", "p2_r1", "p2_r2"}
    expected = {
        "p1_r1": (0.5526, 0.913, 0.2667, 0),
        "p1_r2": (0.5833, 0.6087, 0.3, 0),
        "p2_r1": (0.65, 0.5652, 0.2, 16),
        "p2_r2": (0.625, 0.4348, 0.2, 12),
    }
    for name, values in expected.items():
        metrics = combinations[name]["metrics"]
        assert (
            metrics["reviewed_closed_set_precision"],
            metrics["pooled_known_relevant_retention"],
            metrics["late_reviewed_closed_set_precision"],
            metrics["unlabeled_selected"],
        ) == values
        assert len(combinations[name]["queries"]) == 10
        assert all(run["filter"] for run in combinations[name]["queries"])


def test_batch2d_attributes_every_baseline_relevant_loss_with_evidence():
    report = load_json(REPORT_PATH)
    attribution = report["known_relevant_loss_attribution"]
    overall = attribution["p1_r1_to_p2_r2"]

    assert overall["lost_known_relevant_count"] == 11
    assert overall["counts"] == {
        "planner_coarse_recall_miss": 6,
        "future_section": 0,
        "below_min_score": 4,
        "below_non_character_support": 1,
        "top_k_limit": 0,
        "token_budget_limit": 0,
        "other": 0,
    }
    assert len(overall["items"]) == 11
    assert len({(item["query_index"], item["source_id"]) for item in overall["items"]}) == 11
    assert all(item["evidence_text"].strip() for item in overall["items"])
    assert all(item["v1"]["reason"].startswith("selected:") for item in overall["items"])
    assert attribution["planner_v2_effect_with_reranker_v1"]["lost_known_relevant_count"] == 9
    assert attribution["reranker_v2_effect_with_planner_v1"]["lost_known_relevant_count"] == 7
    assert attribution["reranker_v2_effect_with_planner_v2"]["lost_known_relevant_count"] == 3


def test_batch2d_upper_bounds_prove_review_cannot_change_release_decision():
    report = load_json(REPORT_PATH)
    review = load_json(REVIEW_PATH)

    assert all(
        bound["can_reach_all_quality_gates"] is False
        for bound in report["optimistic_unknown_upper_bounds"].values()
    )
    assert review["summary"] == {
        "candidate_count": 0,
        "eligible_combinations": [],
        "status": "review_not_required",
    }
    assert review["candidates"] == []
    assert _review_has_work(REVIEW_PATH) is False
    assert report["decision"] == {
        "remain_shadow": True,
        "retain_query_planner_v2": False,
        "retain_graded_reranker_direction": False,
        "start_phase4": False,
        "reason": "No tested hybrid both preserves at least 90% pooled known relevance and improves precision.",
    }
