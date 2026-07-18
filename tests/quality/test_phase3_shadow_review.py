import hashlib
import sys

import pytest

from tests.quality.baseline import DEFAULT_RAG, ROOT, load_json
from tests.quality import build_phase3_shadow_review
from tests.quality.phase3_retrieval_eval import human_review_metrics


REVIEW_PATH = ROOT / "tests" / "quality" / "phase3_shadow_candidates_review.json"
REPORT_PATH = ROOT / "reports" / "phase3-shadow-retrieval.json"
EVALUATION_PATH = ROOT / "reports" / "phase3-human-evaluation.json"
REQUIRED_SCORE_COMPONENTS = {
    "vector", "keyword", "title", "character", "chapter_proximity",
}


def _selected_by_query(report: dict) -> dict[int, list[dict]]:
    return {
        int(run["query_index"]): [
            candidate
            for candidate in run["candidate_trace"]
            if candidate["selected"]
        ]
        for run in report["queries"]
    }


def test_phase3_shadow_review_has_exact_query_and_candidate_counts():
    review = load_json(REVIEW_PATH)
    assert review["summary"] == {
        "query_count": 10,
        "candidate_count": 38,
        "human_reviewed_count": 38,
        "status": "human_review_complete",
    }
    assert [entry["query_index"] for entry in review["queries"]] == list(range(1, 11))
    assert sum(entry["candidate_count"] for entry in review["queries"]) == 38


def test_phase3_shadow_review_matches_selected_source_ids_without_query_duplicates():
    review = load_json(REVIEW_PATH)
    expected = _selected_by_query(load_json(REPORT_PATH))
    for entry in review["queries"]:
        query_index = int(entry["query_index"])
        source_ids = [item["source_id"] for item in entry["candidates"]]
        assert len(source_ids) == len(set(source_ids)), query_index
        assert set(source_ids) == {item["id"] for item in expected[query_index]}


def test_phase3_shadow_review_preserves_query_context_and_valid_human_fields():
    review = load_json(REVIEW_PATH)
    annotations = {
        int(entry["query_index"]): entry for entry in load_json(DEFAULT_RAG)["entries"]
    }
    for group in review["queries"]:
        source = annotations[int(group["query_index"])]
        for item in group["candidates"]:
            assert item["query_index"] == group["query_index"]
            assert item["query"] == source["query"]
            assert item["query_intent"] == source["query_intent"]
            assert item["must_recall_facts"] == source["must_recall_facts"]
            assert item["human_relevant"] in {"相关", "不相关", "无法判断"}
            assert item["review_note"].strip()
            assert set(item["supports_which_fact"]) <= set(item["must_recall_facts"])
            if item["human_relevant"] != "相关":
                assert item["supports_which_fact"] == []


def test_phase3_shadow_review_has_valid_evidence_scores_and_reasons():
    review = load_json(REVIEW_PATH)
    report = _selected_by_query(load_json(REPORT_PATH))
    report_by_key = {
        (query_index, candidate["id"]): candidate
        for query_index, candidates in report.items()
        for candidate in candidates
    }
    for group in review["queries"]:
        for item in group["candidates"]:
            key = (item["query_index"], item["source_id"])
            trace = report_by_key[key]
            assert item["source_id"]
            assert item["evidence_text"].strip()
            assert hashlib.sha256(item["evidence_text"].encode("utf-8")).hexdigest() == item["evidence_text_hash"]
            assert item["section"] >= 1
            assert item["subsection"] >= 1
            assert item["title"].strip()
            assert item["selection_reason"] == trace["reason"]
            assert item["selection_reason"].startswith("selected:")
            assert set(item["score_components"]) == REQUIRED_SCORE_COMPONENTS
            assert item["score_components"] == trace["score_components"]
            assert item["final_score"] == trace["final_score"]


def test_review_generator_refuses_to_overwrite_human_table(monkeypatch, tmp_path):
    output = tmp_path / "review.json"
    output.write_text("human work", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["build_phase3_shadow_review", "--output", str(output)])

    with pytest.raises(FileExistsError, match="without --force"):
        build_phase3_shadow_review.main()


def test_completed_phase3_human_metrics_are_frozen():
    metrics = human_review_metrics(load_json(DEFAULT_RAG), load_json(REVIEW_PATH))

    assert metrics["selected_candidates"] == 38
    assert metrics["human_relevant_candidates"] == 21
    assert metrics["human_precision_at_5"] == 0.5526
    assert metrics["gold_section_hits"] == 13
    assert metrics["gold_sections"] == 30
    assert metrics["section_recall_at_5"] == 0.4333
    assert metrics["supported_facts"] == 6
    assert metrics["must_recall_facts"] == 26
    assert metrics["fact_coverage_recall"] == 0.2308
    assert metrics["late_chapter_human_precision_at_5"] == 0.2667
    assert metrics["zero_result_queries"] == [3]


def test_phase3_human_evaluation_report_matches_review_and_keeps_shadow():
    evaluation = load_json(EVALUATION_PATH)
    expected_metrics = human_review_metrics(load_json(DEFAULT_RAG), load_json(REVIEW_PATH))

    assert evaluation["metrics"] == expected_metrics
    assert evaluation["gates"] == {
        "human_precision_at_5_at_least_0_68": False,
        "section_recall_at_5_above_0_6667": False,
        "late_chapter_human_precision_at_5_above_0_40": False,
        "writer_input_and_behavior_unchanged": True,
    }
    assert evaluation["all_release_gates_passed"] is False
    assert evaluation["production_switched"] is False
    assert evaluation["decision"] == "remain_shadow"
