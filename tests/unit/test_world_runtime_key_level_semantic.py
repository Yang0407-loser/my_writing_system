from experiments.world_runtime_writer_canary.wr39_semantic_mapping import (
    KEY_SPECS,
    _comment_count,
    _employment_status,
    _extract_time_range,
    _resignation_lifecycle,
    key_level_compare,
    map_legacy_key,
)


def _fact(fact_type, subject, predicate, value):
    return {
        "fact_type": fact_type,
        "subject": subject,
        "predicate": predicate,
        "value": value,
    }


def test_mapping_specs_are_unique_and_well_formed():
    spec_ids = [spec["spec_id"] for spec in KEY_SPECS.values()]
    # The same spec_id may legitimately back several legacy predicates, but
    # every occurrence must point at exactly one WR key.
    spec_to_wr_keys = {}
    for predicate, spec in KEY_SPECS.items():
        spec_to_wr_keys.setdefault(spec["spec_id"], set()).add(
            tuple(spec["wr_key"])
        )
    assert all(len(keys) == 1 for keys in spec_to_wr_keys.values())
    for predicate, spec in KEY_SPECS.items():
        assert predicate
        assert len(spec["wr_key"]) == 3
        assert spec["mapping_kind"] in {"exact", "approximate"}
        assert spec["compare"] in {"exact", "range_contains", "count_compatible"}


def test_value_normalizers():
    assert _extract_time_range("四点二十至五点之间") == {
        "start": "04:20",
        "end": "05:00",
    }
    assert _comment_count("文章评论区有新的评论") == ">0"
    assert _employment_status("辞职状态未定，但考虑推迟") == "employed"
    assert _resignation_lifecycle("决定推迟辞职") == "private_draft"


def test_map_legacy_key_returns_mapping_kind_and_wr_key():
    mapping = map_legacy_key(
        _fact("temporal_state", "当前时间", "is_early_morning", "四点二十至五点之间")
    )
    assert mapping is not None
    assert mapping["mapping_kind"] == "approximate"
    assert mapping["wr_key"] == ["temporal_state", "world_clock", "time"]
    assert map_legacy_key(
        _fact("character_state", "林晚", "has_not_replied_to_messages", "x")
    ) is None


def test_key_level_matrix_on_frozen_s1_facts():
    legacy = [
        _fact("character_state", "林晚", "has_decided_to_delay_quitting", "决定推迟辞职"),
        _fact("character_state", "林晚", "has_not_replied_to_messages", "未回复群消息"),
        _fact("character_state", "林晚", "has_quit_job", "辞职状态未定，但考虑推迟"),
        _fact("character_state", "林晚", "has_saved_photo_to_drafts", "照片存入草稿箱"),
        _fact("character_state", "林晚", "has_taken_photo_of_zhou_ye", "拍了周野的照片"),
        _fact("character_state", "林晚", "has_written_article", "文章已保存但未发布"),
        _fact("continuity_state", "handover", "handover_character_state", "交接状态"),
        _fact("open_event_chain", "handover", "handover_open_thread", "开放线索"),
        _fact("open_event_chain", "面包店", "has_customers_requesting_preorders", "顾客询问预订"),
        _fact("relationship_state", "林晚和周野", "are_partners_in_bakery", "共同经营面包店"),
        _fact("temporal_state", "当前时间", "is_early_morning", "四点二十至五点之间"),
        _fact("temporal_state", "当前时间", "is_five_am", "五点整"),
    ]
    wr = [
        _fact("temporal_state", "world_clock", "time", "05:00"),
        _fact("continuity_state", "article:lin-wan", "publication_state", "draft"),
        _fact("character_state", "employment:lin-wan", "status", "employed"),
        _fact(
            "continuity_state",
            "resignation:lin-wan",
            "lifecycle_state",
            "private_draft",
        ),
        _fact(
            "presence_state",
            "bakery:wild-bread:storefront",
            "operation_state",
            "closed",
        ),
    ]
    result = key_level_compare(legacy, wr)
    summary = result["summary"]
    assert summary["legacy_fact_count"] == 12
    assert summary["legacy_mapped_count"] == 5
    assert summary["legacy_unmapped_by_design_count"] == 7
    assert summary["status_counts"] == {
        "matched": 4,
        "compatible": 1,
        "unmapped_by_design": 7,
    }
    assert summary["wr_covered_key_count"] == 4
    assert summary["wr_only_key_count"] == 1
    assert summary["wr_only_keys"] == [
        ["presence_state", "bakery:wild-bread:storefront", "operation_state"]
    ]
    rows = {tuple(row["legacy_key"]): row for row in result["matrix"]}
    assert rows[("character_state", "林晚", "has_quit_job")]["status"] == "matched"
    assert rows[("temporal_state", "当前时间", "is_early_morning")]["status"] == "compatible"
    coverage = {
        tuple(row["wr_key"]): row["covered"] for row in result["wr_coverage"]
    }
    assert coverage[("temporal_state", "world_clock", "time")] is True
    assert coverage[
        ("presence_state", "bakery:wild-bread:storefront", "operation_state")
    ] is False


def test_value_mismatch_and_wr_key_absent():
    legacy = [
        _fact("character_state", "林晚", "has_article_comments", "评论区有新的评论"),
        _fact("temporal_state", "当前时间", "is_five_am", "五点整"),
    ]
    wr_zero = [
        _fact(
            "continuity_state",
            "article:lin-wan",
            "public_comment_count",
            0,
        )
    ]
    result = key_level_compare(legacy, wr_zero)
    summary = result["summary"]
    assert summary["status_counts"]["value_mismatch"] == 1
    assert summary["status_counts"]["wr_key_absent"] == 1

    wr_positive = [
        _fact(
            "continuity_state",
            "article:lin-wan",
            "public_comment_count",
            3,
        ),
        _fact("temporal_state", "world_clock", "time", "05:00"),
    ]
    result = key_level_compare(legacy, wr_positive)
    assert result["summary"]["status_counts"] == {
        "matched": 2,
    }
