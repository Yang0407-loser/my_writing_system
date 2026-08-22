from app.retrieval_observability import estimate_candidate_usage, measure_retrieval_usage


def test_exact_candidate_usage_is_detected():
    usage = estimate_candidate_usage(
        "周野每周给父亲留一袋白吐司。",
        "林晚终于明白，周野每周给父亲留一袋白吐司。",
    )
    assert usage["classification"] == "exact_or_near_exact"


def test_unrelated_candidate_is_not_observed():
    usage = estimate_candidate_usage(
        "周野每周给父亲留一袋白吐司。",
        "季晴在办公室里整理年底招聘计划。",
    )
    assert usage["classification"] == "not_observed"


def test_usage_results_keep_document_identity():
    result = measure_retrieval_usage(
        [{"id": "chunk-1", "rank": 3, "text": "天然酵母需要五天培养。"}],
        "这种酵母要培养五天。",
    )
    assert result[0]["id"] == "chunk-1"
    assert result[0]["rank"] == 3
    assert result[0]["heuristic"] is True
