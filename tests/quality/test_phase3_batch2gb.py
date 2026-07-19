from tests.quality.baseline import ROOT, load_json


REPORT = ROOT / "reports" / "phase3-batch2gb-event-shadow-retrieval.json"
REVIEW = ROOT / "tests" / "quality" / "phase3_batch2gb_new_event_review.json"


def test_batch2gb_shadow_is_idempotent_isolated_and_production_unchanged():
    report = load_json(REPORT)
    isolation = report["isolation"]
    assert report["mode"] == "real_event_vector_retrieval_isolated_shadow"
    assert report["collection_strategy"] == "shared_collection_isolated_shadow_task_id"
    assert report["shadow_task_id"] != report["source_task_id"]
    assert isolation["production_query_shadow_hits"] == 0
    assert isolation["shadow_query_production_hits"] == 0
    assert isolation["production_unchanged"] is True
    assert isolation["production_before"] == isolation["production_after"]
    assert isolation["shadow_before"] == isolation["shadow_after"]
    assert isolation["shadow_after"]["count"] == 45
    assert isolation["idempotent_second_add_count"] == 0
    assert isolation["idempotent_second_reused_count"] == 45
    assert report["cleanup"]["execute"] is False
    assert report["cleanup"]["dry_run_match_count"] == 45


def test_batch2gb_real_retrieval_fails_quality_fact_and_token_gates():
    report = load_json(REPORT)
    metrics = report["metrics"]
    assert metrics["parent"]["closed_set_precision"] == 0.5385
    assert metrics["parent"]["known_relevant_retention"] == 0.6087
    assert metrics["parent"]["late_precision"] == 0.2727
    assert metrics["parent"]["gold_section_candidate_proxy"] == 0.7
    assert metrics["context"]["mean_tokens"] == 516.2
    assert metrics["context"]["token_reduction_vs_v1"] == -0.0976
    assert metrics["fact"]["fact_parent_retrieved"] == 8
    assert metrics["fact"]["evidence_preservation_upper_bound"] == 0.8889
    assert report["all_gates_passed"] is False
    assert report["decision"] == "remain_shadow_event_retrieval_failed"


def test_batch2gb_fixed_failures_safely_skip_large_unknown_review():
    report, review = load_json(REPORT), load_json(REVIEW)
    assert report["new_candidate_review"] == {
        "item_count": 0, "reviewed": 0, "suppressed_new_candidates": 25,
        "review_skipped_by_fixed_failure_proof": True, "status": "complete"
    }
    assert review["items"] == []
    assert report["gates"]["bidirectional_isolation_is_1"] is True
    assert report["gates"]["production_task_unchanged"] is True
    assert report["gates"]["parent_closed_set_precision_at_least_0_68"] is False
    assert report["gates"]["known_relevant_parent_retention_at_least_0_90"] is False
    assert report["gates"]["late_parent_precision_above_0_40"] is False
    assert report["gates"]["nine_fact_evidence_preserved"] is False
    assert report["gates"]["token_reduction_at_least_0_20"] is False
