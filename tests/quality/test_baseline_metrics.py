from pathlib import Path

import pytest

from tests.quality.baseline import (
    DEFAULT_RAG,
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
