from app.context_compactor import ContextCompactor, split_sentence_spans


def _source(source_id, text, *, section=1, title="标题", score=0.5):
    return {
        "source_id": source_id,
        "text": text,
        "section": section,
        "subsection": 1,
        "title": title,
        "final_score": score,
    }


def test_sentence_spans_round_trip_exact_original_offsets():
    text = "  林晚删帖。\n周野递来面包！最后一句"

    spans = split_sentence_spans(text)

    assert [text[item.start:item.end] for item in spans] == [item.text for item in spans]
    assert [item.text for item in spans] == ["林晚删帖。", "周野递来面包！", "最后一句"]


def test_compactor_does_not_treat_same_chapter_as_duplicate_without_text_overlap():
    result = ContextCompactor().compact(
        query="林晚删帖 周野面包",
        sources=[
            _source("a", "林晚删除了图文。她走向面包店。", section=5),
            _source("b", "周野凌晨开始揉面。烤箱发出低鸣。", section=5),
        ],
        character_names=["林晚", "周野"],
    )

    assert result["near_duplicate_group_count"] == 0
    assert result["source_retention"] == 1.0
    assert result["represented_source_ids"] == ["a", "b"]


def test_compactor_records_near_duplicate_aliases_and_traceable_fragments():
    common = "林晚站在门口。周野递给她热水。她决定删除图文。"
    result = ContextCompactor(duplicate_threshold=0.7).compact(
        query="林晚删除图文",
        sources=[
            _source("a", common, score=0.9),
            _source("b", common + "槐树叶子沙沙响。", score=0.8),
        ],
        character_names=["林晚", "周野"],
    )

    assert result["near_duplicate_group_count"] == 1
    assert result["duplicate_groups"][0]["canonical_source_id"] == "a"
    assert result["folded_characters"] > 0
    for fragment in result["fragments"]:
        source = common if fragment["source_id"] == "a" else common + "槐树叶子沙沙响。"
        assert source[fragment["start"]:fragment["end"]] == fragment["text"]


def test_soft_budget_reports_overflow_without_dropping_sources():
    result = ContextCompactor(
        max_anchor_sentences=1, neighbor_radius=0, soft_token_budget=2
    ).compact(
        query="证据",
        sources=[
            _source("a", "第一条唯一证据。无关句。"),
            _source("b", "第二条唯一证据。另一句。"),
        ],
    )

    assert result["compacted_tokens"] > 2
    assert result["budget_overflow_reason"]
    assert result["represented_source_ids"] == ["a", "b"]
