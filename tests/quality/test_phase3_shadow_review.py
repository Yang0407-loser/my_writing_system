import hashlib
import sys

import pytest

from tests.quality.baseline import DEFAULT_RAG, ROOT, load_json
from tests.quality import build_phase3_shadow_review


REVIEW_PATH = ROOT / "tests" / "quality" / "phase3_shadow_candidates_review.json"
REPORT_PATH = ROOT / "reports" / "phase3-shadow-retrieval.json"
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
        "human_reviewed_count": 0,
        "status": "awaiting_human_review",
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


def test_phase3_shadow_review_preserves_query_context_and_blank_human_fields():
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
            assert item["human_relevant"] == ""
            assert item["supports_which_fact"] == []
            assert item["review_note"] == ""


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
