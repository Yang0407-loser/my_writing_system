from tests.benchmarks.benchmark_phase3_2d import _upper_bound


def test_upper_bound_treats_unknown_as_relevant_but_expands_relevant_pool():
    metrics = {
        "known_relevant_selected": 10,
        "known_irrelevant_selected": 6,
        "unlabeled_selected": 12,
        "per_query": [
            {"query_index": 1, "known_relevant": 1, "known_irrelevant": 1, "unlabeled": 3},
            {"query_index": 4, "known_relevant": 0, "known_irrelevant": 1, "unlabeled": 3},
            {"query_index": 10, "known_relevant": 0, "known_irrelevant": 2, "unlabeled": 3},
        ],
    }

    result = _upper_bound(metrics, known_relevant_total=23)

    assert result["maximum_closed_set_precision"] == 0.7857
    assert result["maximum_pooled_known_relevant_retention"] == 0.6286
    assert result["maximum_late_precision"] == 0.7143
    assert result["can_reach_all_quality_gates"] is False
