from experiments.world_runtime_writer_canary.wr39_semantic_mapping import (
    map_legacy_fact,
    semantic_compare,
)


def _fact(fact_type, subject, predicate, value):
    return {"fact_type": fact_type, "subject": subject, "predicate": predicate, "value": value}


def test_time_predicates_map_to_canonical_clock():
    mapped = map_legacy_fact(_fact("temporal_state", "当前时间", "is_five_am", "五点整"))
    assert mapped == {
        "fact_type": "temporal_state",
        "subject": "world_clock",
        "predicate": "time",
        "value": "05:00",
    }
    mapped = map_legacy_fact(_fact("temporal_state", "林晚", "published_at_5_59", "在五点五十九分发布文章"))
    assert mapped["value"] == "05:59"


def test_publication_predicates_map_to_article_state():
    published = map_legacy_fact(_fact("open_event_chain", "林晚", "published_article", "发布了文章"))
    assert published == {
        "fact_type": "continuity_state",
        "subject": "article:lin-wan",
        "predicate": "publication_state",
        "value": "published",
    }
    draft = map_legacy_fact(_fact("character_state", "林晚", "has_written_article", "文章已保存但未发布"))
    assert draft["value"] == "draft"


def test_comments_predicate_maps_with_value_probe():
    mapped = map_legacy_fact(_fact("character_state", "林晚", "has_article_comments", "评论区有新的评论"))
    assert mapped["predicate"] == "public_comment_count"
    assert mapped["value"] == ">0"


def test_unmapped_narrative_predicates_return_none():
    for predicate in ("has_not_replied_to_messages", "has_taken_photo_of_zhou_ye", "are_partners_in_bakery"):
        assert map_legacy_fact(_fact("character_state", "林晚", predicate, "x")) is None


def test_semantic_compare_reports_matched_and_divergence():
    legacy = [
        _fact("temporal_state", "当前时间", "is_five_am", "五点整"),
        _fact("character_state", "林晚", "has_taken_photo_of_zhou_ye", "拍了照片"),
        _fact("character_state", "林晚", "has_article_comments", "评论区有新的评论"),
    ]
    wr = [
        _fact("temporal_state", "world_clock", "time", "05:00"),
        _fact("continuity_state", "article:lin-wan", "public_comment_count", 0),
        _fact("presence_state", "bakery:wild-bread:storefront", "operation_state", "closed"),
    ]
    result = semantic_compare(legacy, wr)
    assert result["legacy_mapped_count"] == 2
    assert result["legacy_unmapped_by_design_count"] == 1
    assert result["matched_fact_keys"] == 1
    assert result["value_mismatch_count"] == 1
    assert result["wr_only_fact_keys"] == [["presence_state", "bakery:wild-bread:storefront", "operation_state"]]
    assert result["legacy_only_mapped_fact_keys"] == []
