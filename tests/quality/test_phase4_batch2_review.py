import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_phase4_batch2_codex_review_contract():
    payload = json.loads(
        (ROOT / "tests" / "quality" / "phase4_batch2_codex_review.json").read_text(encoding="utf-8")
    )

    assert payload["review_provenance"] == "codex_assisted_review"
    assert payload["independent_human_confirmation"] is False
    reviews = payload["reviews"]
    assert [item["query_index"] for item in reviews] == list(range(1, 11))
    assert sum(item["mapping_exposed_before_review"] for item in reviews) == 1
    assert all(item["winner_candidate"] in {"candidate_1", "candidate_2", "tie"} for item in reviews)
    for item in reviews:
        assert item["short_evidence"]
        for candidate_id in ("candidate_1", "candidate_2"):
            judgment = item[candidate_id]
            assert isinstance(judgment["goal_complete"], bool)
            for field in ("hard_violations", "relationship_violations", "continuity_defects", "causality_defects", "fact_errors"):
                assert isinstance(judgment[field], int) and judgment[field] >= 0
