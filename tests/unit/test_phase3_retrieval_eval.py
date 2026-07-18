from tests.quality.phase3_retrieval_eval import manual_label_metrics, section_proxy_metrics, text_hash


def test_section_proxy_metrics_counts_unique_gold_recall():
    entries = [
        {"query_index": 1, "section": 13, "gold_sections": [2, 3]},
        {"query_index": 2, "section": 4, "gold_sections": [1]},
    ]
    runs = [
        {
            "query_index": 1, "selected_sections": [2, 2, 9],
            "candidate_count": 8, "elapsed_ms": 10, "estimated_context_tokens": 100,
        },
        {
            "query_index": 2, "selected_sections": [1],
            "candidate_count": 4, "elapsed_ms": 20, "estimated_context_tokens": 50,
        },
    ]

    metrics = section_proxy_metrics(entries, runs)

    assert metrics["precision_at_5_section_proxy"] == 0.75
    assert metrics["recall_at_5_section_proxy"] == 0.6667
    assert metrics["late_chapter_precision_at_5_section_proxy"] == 0.6667
    assert metrics["mean_candidate_pool"] == 6


def test_manual_label_metrics_refuses_unlabeled_production_gate():
    known = "旧候选"
    entries = [{
        "query_index": 1,
        "items": [{"text": known, "human_relevant": "相关"}],
    }]
    runs = [{
        "query_index": 1,
        "selected_text_hashes": [text_hash(known), text_hash("新候选")],
    }]

    metrics = manual_label_metrics(entries, runs)

    assert metrics["human_label_coverage"] == 0.5
    assert metrics["precision_at_5_on_labeled"] == 1.0
    assert metrics["production_gate_valid"] is False
