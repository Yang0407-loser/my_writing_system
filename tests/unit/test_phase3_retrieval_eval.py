from tests.quality.phase3_retrieval_eval import (
    human_review_failure_observations,
    human_review_metrics,
    manual_label_metrics,
    section_proxy_metrics,
    text_hash,
)


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


def test_human_review_metrics_keep_section_and_fact_recall_separate():
    annotation = {"entries": [
        {
            "query_index": 1, "section": 13,
            "gold_sections": [2, 3], "must_recall_facts": ["A", "B"],
        },
        {
            "query_index": 2, "section": 4,
            "gold_sections": [1], "must_recall_facts": ["C"],
        },
    ]}
    review = {"queries": [
        {"query_index": 1, "candidates": [
            {"human_relevant": "相关", "section": 2, "supports_which_fact": ["A"]},
            {"human_relevant": "不相关", "section": 3, "supports_which_fact": []},
        ]},
        {"query_index": 2, "candidates": []},
    ]}

    metrics = human_review_metrics(annotation, review)

    assert metrics["human_precision_at_5"] == 0.5
    assert metrics["section_recall_at_5"] == 0.3333
    assert metrics["fact_coverage_recall"] == 0.3333
    assert metrics["late_chapter_human_precision_at_5"] == 0.5
    assert metrics["zero_result_queries"] == [2]


def test_human_failure_observations_report_layers_without_causal_claim():
    review = {"queries": [
        {"query_index": 1, "candidates": [
            {
                "review_item_id": "q01-c01", "query_index": 1,
                "source_id": "bad", "human_relevant": "不相关",
                "supports_which_fact": [], "matched_intents": ["character"],
                "score_components": {"character": 1.0}, "final_score": 0.5,
                "selection_reason": "selected:intents=character;strongest=character",
            },
            {
                "review_item_id": "q01-c02", "query_index": 1,
                "source_id": "partial", "human_relevant": "相关",
                "supports_which_fact": [], "matched_intents": ["event"],
                "score_components": {"character": 0.0}, "final_score": 0.4,
                "selection_reason": "selected:intents=event;strongest=vector",
            },
        ]},
        {"query_index": 2, "candidates": []},
    ]}

    observations = human_review_failure_observations(review)

    assert observations["rule_rerank_deviation"]["selected_false_positives"] == 1
    assert observations["partial_fact_support"]["candidate_count"] == 1
    assert observations["zero_result_queries"]["query_indices"] == [2]
    assert observations["query_planner_intent_deviation"]["status"] == "inference_not_causal_proof"
