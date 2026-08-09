from experiments.world_runtime_writer_canary.wr310_consumer_read_switch_audit import (
    aggregate_consumer,
    audit_consumer,
)


def _key_result():
    return {
        "wr_coverage": [
            {
                "wr_key": ["temporal_state", "world_clock", "time"],
                "wr_value": "05:00",
                "covered": True,
                "covered_by_legacy_keys": [
                    ["temporal_state", "当前时间", "is_five_am"]
                ],
            },
            {
                "wr_key": ["character_state", "employment:lin-wan", "status"],
                "wr_value": "employed",
                "covered": True,
                "covered_by_legacy_keys": [
                    ["character_state", "林晚", "has_quit_job"]
                ],
            },
            {
                "wr_key": [
                    "character_state",
                    "character:ji-qing",
                    "article_knowledge",
                ],
                "wr_value": "unknown",
                "covered": False,
                "covered_by_legacy_keys": [],
            },
            {
                "wr_key": [
                    "presence_state",
                    "bakery:wild-bread:storefront",
                    "operation_state",
                ],
                "wr_value": "closed",
                "covered": False,
                "covered_by_legacy_keys": [],
            },
        ],
    }


def _handover_projection():
    return {
        "field_coverage": {
            "character_state": {
                "status": "projected_from_wr",
                "item_count": 3,
            },
            "open_threads": {
                "status": "projected_from_wr",
                "item_count": 2,
            },
            "new_facts": {
                "status": "projected_from_wr",
                "item_count": 2,
            },
            "foreshadowing": {
                "status": "legacy_only_not_projected",
                "item_count": 0,
            },
            "found_contradictions": {
                "status": "legacy_only_not_projected",
                "item_count": 0,
            },
            "arc_progress": {
                "status": "legacy_only_not_projected",
                "item_count": 0,
            },
        }
    }


def _rag_projection():
    return {
        "metadata": {
            "characters": ["林晚", "周野", "季晴", "老吴"],
            "time": "05:00",
            "weekday": "saturday",
            "locations": ["bakery:wild-bread:workshop"],
            "world_revision": 8,
        },
        "coverage": {
            "characters_status": "projected_from_wr",
            "time_status": "projected_from_wr",
            "weekday_status": "projected_from_wr",
            "locations_status": "projected_from_wr",
        },
    }


def test_handover_audit_marks_projected_and_legacy_only_fields():
    audit = audit_consumer("handover", _handover_projection(), _key_result())
    fields = {item["field"]: item for item in audit["fields"]}
    assert fields["character_state"]["status"] == "projected_from_wr"
    assert fields["foreshadowing"]["status"] == "legacy_only_not_projected"
    assert fields["foreshadowing"]["item_count"] == 0
    assert audit["summary"]["wr_key_count"] == 4
    assert audit["summary"]["wr_only_key_count"] == 2
    assert ["character_state", "character:ji-qing", "article_knowledge"] in [
        list(key) for key in audit["summary"]["wr_only_keys"]
    ]


def test_rag_selector_includes_employment_subject():
    audit = audit_consumer("rag_metadata", _rag_projection(), _key_result())
    footprint_keys = [tuple(row["wr_key"]) for row in audit["wr_key_footprint"]]
    assert ("temporal_state", "world_clock", "time") in footprint_keys
    assert ("character_state", "employment:lin-wan", "status") in footprint_keys
    assert audit["summary"]["gaps"] == []


def test_aggregate_recommendation_switch_ready_accept_wr_only():
    footprint = [
        {
            "wr_key": [
                "character_state",
                "character:ji-qing",
                "article_knowledge",
            ],
            "wr_value": "unknown",
            "legacy_equivalent": False,
            "covered_by_legacy_keys": [],
        },
        {
            "wr_key": ["character_state", "employment:lin-wan", "status"],
            "wr_value": "employed",
            "legacy_equivalent": True,
            "covered_by_legacy_keys": [
                ["character_state", "林晚", "has_quit_job"]
            ],
        },
    ]
    audits = [
        {
            "fields": [
                {"field": name, "kind": kind, "status": status, "item_count": count}
                for name, kind, status, count in (
                    ("character_state", "fact_field", "projected_from_wr", 3),
                    ("open_threads", "fact_field", "projected_from_wr", 2),
                    ("new_facts", "fact_field", "projected_from_wr", 2),
                    ("foreshadowing", "legacy_only", "legacy_only_not_projected", 0),
                    ("found_contradictions", "legacy_only", "legacy_only_not_projected", 0),
                    ("arc_progress", "legacy_only", "legacy_only_not_projected", 0),
                )
            ],
            "wr_key_footprint": footprint,
            "summary": {"wr_only_keys": []},
        }
    ]
    result = aggregate_consumer("handover", audits)
    assert result["recommendation"] == "switch_ready_accept_wr_only"
    assert result["wr_only_keys"] == [
        ["character_state", "character:ji-qing", "article_knowledge"]
    ]


def test_aggregate_blocks_when_legacy_only_field_has_data():
    audits = [
        {
            "fields": [
                {"field": name, "kind": kind, "status": status, "item_count": count}
                for name, kind, status, count in (
                    ("character_state", "fact_field", "projected_from_wr", 3),
                    ("open_threads", "fact_field", "projected_from_wr", 2),
                    ("new_facts", "fact_field", "projected_from_wr", 2),
                    ("foreshadowing", "legacy_only", "legacy_only_not_projected", 2),
                    ("found_contradictions", "legacy_only", "legacy_only_not_projected", 0),
                    ("arc_progress", "legacy_only", "legacy_only_not_projected", 0),
                )
            ],
            "wr_key_footprint": [],
            "summary": {"wr_only_keys": []},
        }
    ]
    result = aggregate_consumer("handover", audits)
    assert result["recommendation"] == "blocked_legacy_only_data_loss"


def test_reviewer_always_needs_side_by_side_decision():
    audits = [
        {
            "fields": [
                {"field": "handover_chain", "kind": "fact_field", "status": "projected_from_wr", "item_count": 1},
                {"field": "character_consistency_context", "kind": "fact_field", "status": "projected_from_wr", "item_count": 1},
                {"field": "relation_context", "kind": "legacy_only", "status": "legacy_only_not_projected", "item_count": 0},
                {"field": "subplot_context", "kind": "legacy_only", "status": "legacy_only_not_projected", "item_count": 0},
            ],
            "wr_key_footprint": [],
            "summary": {"wr_only_keys": []},
        }
    ]
    result = aggregate_consumer("reviewer", audits)
    assert result["recommendation"] == "needs_side_by_side_decision"


def test_aggregate_covered_anywhere_is_not_wr_only():
    key = ["temporal_state", "world_clock", "time"]
    fields = [
        {"field": "characters", "kind": "fact_field", "status": "projected_from_wr", "item_count": 3},
        {"field": "time", "kind": "fact_field", "status": "projected_from_wr", "item_count": 1},
        {"field": "weekday", "kind": "fact_field", "status": "projected_from_wr", "item_count": 1},
        {"field": "locations", "kind": "fact_field", "status": "projected_from_wr", "item_count": 1},
        {"field": "world_revision", "kind": "meta", "status": "meta_projected", "item_count": 1},
    ]
    audits = [
        {
            "fields": fields,
            "wr_key_footprint": [
                {
                    "wr_key": key,
                    "wr_value": "05:00",
                    "legacy_equivalent": True,
                    "covered_by_legacy_keys": [
                        ["temporal_state", "当前时间", "is_five_am"]
                    ],
                }
            ],
            "summary": {"wr_only_keys": []},
        },
        {
            "fields": fields,
            "wr_key_footprint": [
                {
                    "wr_key": key,
                    "wr_value": "07:20",
                    "legacy_equivalent": False,
                    "covered_by_legacy_keys": [],
                }
            ],
            "summary": {"wr_only_keys": []},
        },
    ]
    result = aggregate_consumer("rag_metadata", audits)
    assert result["wr_only_key_count"] == 0
