from pathlib import Path

import pytest

from tests.quality.baseline import (
    DEFAULT_CHARACTER,
    DEFAULT_RAG,
    DEFAULT_STYLE,
    compute_character_metrics,
    compute_rag_metrics,
    load_json,
    split_numbered_sections,
    style_stats,
)


def test_manual_rag_baseline_is_reproducible():
    metrics = compute_rag_metrics(load_json(DEFAULT_RAG))
    assert metrics["queries"] == 10
    assert metrics["precision_at_5"] == 0.68
    assert metrics["recall_at_5"] == 0.6667


def test_rag_schema_rejects_missing_required_context():
    annotation = {
        "k": 5,
        "entries": [{"query_index": 1, "items": [], "gold_sections": []}],
    }
    with pytest.raises(ValueError, match="missing fields"):
        compute_rag_metrics(annotation)


def test_style_stats_recognize_chinese_dialogue_quotes():
    stats = style_stats("他说：“现在出发。”\n\n风很冷，门外传来沙沙声。")
    assert stats["dialogue_ratio"] > 0
    assert stats["sensory_terms_per_1k"] > 0


def test_split_numbered_sections():
    sections = split_numbered_sections("第1节：开始\n正文\n第2节：继续\n正文二")
    assert sorted(sections) == [1, 2]
    assert "正文二" in sections[2]


def test_human_character_annotation_counts_are_consistent():
    annotation = load_json(DEFAULT_CHARACTER)
    constraints = annotation["constraints"]
    hard = [item for item in constraints if item["hardness"] == "hard"]
    confirmed = [item for item in hard if item["review_status"] == "human_confirmed"]
    issues = [item for item in hard if item["review_status"] == "human_flagged_issue"]

    assert len(constraints) == 30
    assert len(hard) == 19
    assert len(confirmed) == 17
    assert len(issues) == 2
    assert {item["id"] for item in issues} == {"linwan-10", "jiqing-10"}
    assert all(item["observed_status"] == "satisfied" for item in confirmed)
    assert all(item["observed_status"] == "violated" for item in issues)


def test_human_review_summary_and_metrics_match_annotations():
    annotation = load_json(DEFAULT_CHARACTER)
    summary = annotation["human_review"]
    metrics = compute_character_metrics(annotation)

    assert summary["hard_total"] == metrics["hard_constraints"] == 19
    assert summary["human_confirmed"] == metrics["human_confirmed_hard"] == 17
    assert summary["human_flagged_issue"] == metrics["human_flagged_issue_hard"] == 2
    assert set(summary["issue_rule_ids"]) == set(metrics["human_issue_rule_ids"])
    assert metrics["human_label_coverage"] == 1.0
    assert metrics["human_hard_violation_rate"] == 0.1053
    assert metrics["release_gate_ready"] is True


def test_character_evidence_sections_are_valid():
    annotation = load_json(DEFAULT_CHARACTER)
    max_section = 18

    for item in annotation["constraints"]:
        sections = item["evidence_sections"]
        assert sections, item["id"]
        assert all(type(section) is int for section in sections), item["id"]
        assert all(1 <= section <= max_section for section in sections), item["id"]
        assert len(sections) == len(set(sections)), item["id"]

    relation_rule = next(item for item in annotation["constraints"] if item["id"] == "linwan-10")
    assert relation_rule["rule_scope"] == "relationship_stage"
    assert relation_rule["related_characters"] == ["林晚", "周野"]
    assert relation_rule["evidence_sections"] == [7, 12, 16, 18]


def test_style_human_issue_is_separate_from_four_control_contract():
    style = load_json(DEFAULT_STYLE)
    issues = style["qualitative_issues"]

    assert [item["id"] for item in issues] == ["style-human-01"]
    assert issues[0]["status"] == "baseline_issue"
    assert set(issues[0]["dimensions"]) == {
        "mechanical_counting",
        "repetitive_sentence_patterns",
        "insufficient_emotional_layering",
    }
    assert "不恢复旧 50 维字段" in issues[0]["contract_decision"]
