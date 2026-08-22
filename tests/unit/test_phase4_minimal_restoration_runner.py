from tests.benchmarks.run_minimal_restoration_experiment import (
    arm_restorations,
    restoration_groups,
    restore_items,
)


def _run():
    return {
        "profile": "budgeted_broker",
        "items": [
            {"item_id": "p0", "source_type": "fixed_prompt", "priority": "P0", "keep": True},
            {"item_id": "old", "source_type": "recent_original", "priority": "P3", "keep": False, "drop_reason": "budget"},
            {"item_id": "world", "source_type": "world_event", "priority": "P3", "keep": False, "drop_reason": "budget"},
            {"item_id": "style", "source_type": "style_examples", "priority": "P3", "keep": False, "drop_reason": "budget"},
        ],
    }


def test_restoration_groups_cover_only_dropped_optional_items():
    assert restoration_groups(_run()) == {
        "recent_originals": ["old"],
        "style_context": ["style"],
        "world_events": ["world"],
    }


def test_restore_items_is_copy_only_and_rejects_non_dropped_items():
    source = _run()
    restored = restore_items(source, ["old"])
    assert source["items"][1]["keep"] is False
    assert restored["items"][1]["keep"] is True
    assert restored["items"][1]["keep_reason"] == "minimal_restoration_experiment"
    try:
        restore_items(source, ["p0"])
    except ValueError as exc:
        assert "not restorable" in str(exc)
    else:
        raise AssertionError("protected item must not be restorable")


def test_grouped_and_single_modes_have_controls():
    grouped = arm_restorations(_run(), "grouped")
    singles = arm_restorations(_run(), "singles")
    assert grouped["budgeted_broker"] == []
    assert grouped["legacy_full"] == ["old", "style", "world"]
    assert set(name for name in grouped if name.startswith("restore_group:")) == {
        "restore_group:recent_originals",
        "restore_group:style_context",
        "restore_group:world_events",
    }
    assert set(name for name in singles if name.startswith("restore_item:")) == {
        "restore_item:old", "restore_item:style", "restore_item:world"
    }
