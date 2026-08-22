from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from experiments.world_runtime_writer_canary import wr3_shadow_audit as audit
from experiments.world_runtime_writer_canary.wr310_reviewer_side_by_side import (
    aggregate_reports,
    build_subsection_report,
    compare_context_field,
    compare_handover_field,
    render_handover_chain_text,
)


def _gold_committed():
    gold = build_saturday_bakery_gold_fixture()
    delta, validation = audit._gold_committable(gold)
    return WorldRuntimeStateCommitter().commit(
        idempotency_key="wr3.10:reviewer:gold",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )


def _coverage():
    return {
        field: {"status": "projected_from_wr", "item_count": 0}
        for field in ("character_state", "open_threads", "new_facts")
    } | {
        field: {"status": "legacy_only_not_projected", "item_count": 0}
        for field in ("foreshadowing", "found_contradictions", "arc_progress")
    }


def test_render_handover_chain_text_matches_global_review_format():
    text = render_handover_chain_text([
        {
            "from_section": 1,
            "to_section": 2,
            "foreshadowing": "笔记本越来越厚",
        }
    ])
    assert text == "第1节→第2节: 伏笔=笔记本越来越厚"


def test_compare_handover_field_projected_and_legacy_only():
    coverage = _coverage()
    projected = compare_handover_field(
        "character_state", ["a"], ["a"], coverage
    )
    assert projected["status"] == "projected_from_wr"
    assert projected["value_status"] == "identical"
    different = compare_handover_field(
        "character_state", ["a"], ["b"], coverage
    )
    assert different["value_status"] == "different"
    legacy_only = compare_handover_field(
        "foreshadowing", [], [], coverage
    )
    assert legacy_only["status"] == "legacy_only_not_projected"
    assert legacy_only["value_status"] == "legacy_only"


def test_compare_context_field_marks_legacy_snapshot_unavailable():
    wr_reviewer = {
        "character_consistency_context": "角色数据",
        "relation_context": "（无关系数据）",
        "subplot_context": "（无支线数据）",
        "coverage": {
            "relation_context_status": "legacy_only_not_projected",
            "subplot_context_status": "legacy_only_not_projected",
        },
    }
    character = compare_context_field(
        "character_consistency_context", wr_reviewer
    )
    assert character["wr_status"] == "projected_from_wr"
    assert character["legacy_status"] == "unavailable_in_frozen_snapshot"
    relation = compare_context_field("relation_context", wr_reviewer)
    assert relation["wr_status"] == "legacy_only_not_projected"
    assert "build_relation_context" in relation["legacy_provider"]


def test_subsection_report_and_aggregate_recommendation():
    committed = _gold_committed()
    legacy_note = {
        "foreshadowing": [],
        "character_state": [],
        "open_threads": [],
        "new_facts": [],
        "found_contradictions": [],
        "arc_progress": [],
        "from_section": 0,
        "to_section": 1,
    }
    report = build_subsection_report(1, committed, legacy_note)
    summary = report["summary"]
    assert summary["field_count"] == 9
    assert summary["projected_field_count"] == 4
    assert summary["legacy_only_field_count"] == 5
    assert summary["legacy_provider_unavailable_count"] == 3
    aggregate = aggregate_reports([report])
    assert aggregate["recommendation"] == (
        "handover_chain_comparable;"
        "character_relation_subplot_need_real_task_snapshot"
    )
    assert aggregate["legacy_sources_still_required"] == [
        "character_consistency_context",
        "relation_context",
        "subplot_context",
    ]
