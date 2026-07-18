from tests.benchmarks.ablate_phase3_shadow import _review_has_human_work
from tests.quality.baseline import ROOT, load_json


REPORT_PATH = ROOT / "reports" / "phase3-batch2a-ablation.json"
DIFF_PATH = ROOT / "tests" / "quality" / "phase3_batch2_new_candidates_review.json"
ORIGINAL_REVIEW_PATH = ROOT / "tests" / "quality" / "phase3_shadow_candidates_review.json"


def test_batch2a_report_freezes_grid_baseline_and_shadow_contract():
    report = load_json(REPORT_PATH)

    assert report["mode"] == "offline_trace_replay"
    assert report["production_changed"] is False
    assert report["writer_changed"] is False
    assert report["grid"]["configurations"] == 10_368
    assert report["baseline"]["selected_candidates"] == 38
    assert report["baseline"]["known_relevant_selected"] == 21
    assert report["baseline"]["known_irrelevant_selected"] == 17
    assert report["baseline"]["unlabeled_selected"] == 0
    assert report["baseline"]["closed_set_precision"] == 0.5526
    assert report["decision"] == "remain_shadow_after_targeted_review"


def test_batch2a_selected_pareto_results_and_loo_are_frozen():
    report = load_json(REPORT_PATH)
    selected = report["selected_pareto_configs"]

    assert len(selected) <= 3
    assert [item["config"]["config_id"] for item in selected] == [
        "cfg-03483", "cfg-07777",
    ]
    assert [item["closed_set_precision"] for item in selected] == [0.6364, 0.6667]
    assert [item["known_relevant_retention"] for item in selected] == [1.0, 0.7619]
    assert report["leave_one_query_out"]["aggregate"] == {
        "known_relevant_selected": 20,
        "known_irrelevant_selected": 13,
        "unlabeled_selected": 1,
        "closed_set_precision": 0.6061,
        "known_relevant_retention": 0.9524,
    }


def test_new_candidate_review_contains_only_completed_targeted_differences():
    review = load_json(DIFF_PATH)
    original = load_json(ORIGINAL_REVIEW_PATH)
    original_keys = {
        (int(group["query_index"]), str(candidate["source_id"]))
        for group in original["queries"]
        for candidate in group["candidates"]
    }

    assert review["summary"] == {
        "candidate_count": 2,
        "human_reviewed_count": 2,
        "status": "human_review_complete",
    }
    keys = []
    for candidate in review["candidates"]:
        key = (int(candidate["query_index"]), str(candidate["source_id"]))
        keys.append(key)
        assert key not in original_keys
        assert candidate["query"].strip()
        assert candidate["must_recall_facts"]
        assert candidate["evidence_text"].strip()
        assert candidate["selected_by_configs"]
        assert candidate["review_provenance"] == "codex_assisted_review"
        assert candidate["human_relevant"] == "相关"
        assert candidate["supports_which_fact"] == []
        assert candidate["review_note"].strip()
    assert len(keys) == len(set(keys)) == 2
    assert _review_has_human_work(DIFF_PATH) is True


def test_targeted_review_outcome_updates_metrics_without_reselecting_configs():
    report = load_json(REPORT_PATH)
    outcome = report["targeted_review_outcome"]

    assert outcome["reviewed_relevant"] == 2
    assert outcome["review_provenance"] == "codex_assisted_review"
    assert outcome["independent_human_confirmation"] is False
    assert outcome["reviewed_irrelevant"] == 0
    assert outcome["newly_supported_must_recall_facts"] == 0
    assert outcome["pooled_known_relevant_candidates"] == 23
    by_id = {
        item["config_id"]: item for item in outcome["post_review_comparison"]
    }
    assert by_id["cfg-03483"]["closed_set_precision"] == 0.6571
    assert by_id["cfg-03483"]["pooled_known_relevant_retention"] == 1.0
    assert by_id["cfg-07777"]["closed_set_precision"] == 0.6667
    assert by_id["cfg-07777"]["pooled_known_relevant_retention"] == 0.6957
    assert outcome["frozen_loo_selection_re_evaluation"] == {
        "known_relevant_selected": 21,
        "known_irrelevant_selected": 13,
        "unlabeled_selected": 0,
        "closed_set_precision": 0.6176,
        "pooled_known_relevant_retention": 0.913,
    }


def test_human_review_overwrite_guard_detects_completed_fields(tmp_path):
    path = tmp_path / "review.json"
    path.write_text(
        '{"candidates":[{"human_relevant":"\\u76f8\\u5173",'
        '"supports_which_fact":[],"review_note":"checked"}]}',
        encoding="utf-8",
    )

    assert _review_has_human_work(path) is True
