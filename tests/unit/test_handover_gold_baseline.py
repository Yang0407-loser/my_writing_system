"""Handover 金标基线守卫（任务 3650fd64，V2.2 Demo #4，worked example 批次）。

金标夹具冻结了项目第一次达到 3/4 小节覆盖的真实运行：输入侧全链
（小节正文、大纲、source registry、next boundary）+ 结局数字
（20/33 本地恢复、11 accepted、拒绝分布）。本文件是它的两道守卫：

1. 基线数字防漂移——夹具内容被改动（哪怕一个数字）测试即红；
2. hash 链确定性——registry/boundary/note 构建或 hash 函数的任何
   行为变化都会使重建 hash 偏离冻结值，在花任何 token 之前暴露
   "修一处坏一处"型回归。

夹具中 accepted claim 的 typed 全文不存在（sidecar 按隐私设计不持久化
原始 payload 与 typed contract），因此本守卫覆盖输入侧与 note 投影，
不覆盖 validator 逐条重放——该缺口的补齐是独立批次（payload 持久化）。

夹具再生：python tests/benchmarks/freeze_handover_gold.py（hash 不符拒写）。
"""

import json
from pathlib import Path

from app.writing.handover_contract_v2 import (
    _outline_text,
    build_handover_sources,
    compile_next_boundary,
    sha256_json,
    sha256_text,
)
from app.writing.handover_contract_v21 import build_compact_source_registry

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "subsection_handover_gold_v1.json"
)
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

# Demo #4 账面数字（reports/handover-contract-v22-real-demo2-2026-07-26）。
GOLD_BASELINE = {
    "emitted_items": 33,
    "restored_local_layer": 20,
    "accepted_validator_layer": 11,
    "locally_rejected": 13,
    "rejected_total": 22,
    "completed_with_changes": 3,
    "subsection_total": 4,
    "rejection_distribution": {
        "quote_not_found": 9,
        "evidence_text_mismatch": 9,
        "invalid_quote": 2,
        "unsupported_open_event_component": 2,
    },
}
# (subsection, execution_status, restored, locally_rejected, accepted, rejected)
GOLD_PER_SUBSECTION = (
    (1, "completed_with_changes", 6, 4, 2, 8),
    (2, "completed_with_changes", 6, 1, 4, 3),
    (3, "completed_no_change", 2, 7, 0, 9),
    (4, "completed_with_changes", 6, 1, 5, 2),
)


def test_fixture_identity():
    assert FIXTURE["fixture"] == "subsection-handover-gold-v1"
    assert FIXTURE["contract_version"] == "v2.2"
    assert FIXTURE["payload_version"] == "2.2"
    assert FIXTURE["producer_version"] == "writer-handover-contract-v2.2"
    assert FIXTURE["provenance"]["task_id"].startswith("3650fd64")
    assert len(FIXTURE["subsections"]) == 4


def test_baseline_numbers_pinned():
    baseline = dict(FIXTURE["baseline"])
    distribution = baseline.pop("rejection_distribution")
    expected = dict(GOLD_BASELINE)
    expected_distribution = expected.pop("rejection_distribution")
    assert baseline == expected
    assert distribution == expected_distribution


def test_per_subsection_counts_pinned():
    got = tuple(
        (
            item["subsection"],
            item["execution_status"],
            item["counts"]["restored_claim_count"],
            item["counts"]["locally_rejected_claim_count"],
            item["counts"]["accepted_claim_count"],
            item["counts"]["rejected_claim_count"],
        )
        for item in FIXTURE["subsections"]
    )
    assert got == GOLD_PER_SUBSECTION


def test_counts_internally_consistent():
    baseline = FIXTURE["baseline"]
    subsections = FIXTURE["subsections"]
    assert baseline["emitted_items"] == sum(
        item["counts"]["restored_claim_count"]
        + item["counts"]["locally_rejected_claim_count"]
        for item in subsections
    )
    assert baseline["accepted_validator_layer"] == sum(
        item["counts"]["accepted_claim_count"] for item in subsections
    )
    for item in subsections:
        counts = item["counts"]
        assert counts["accepted_claim_count"] <= counts["restored_claim_count"]
        distribution_sum = sum((item["rejection_counts"] or {}).values())
        assert distribution_sum == counts["rejected_claim_count"]


def test_no_shape_family_rejections_in_gold():
    """Demo #4 的定义性事实：worked example 后 arity/形状类拒绝为零。"""
    for item in FIXTURE["subsections"]:
        assert item["rejection_shape_skeletons"] is None
        for reason in item["rejection_counts"]:
            assert not reason.startswith("invalid_claim_shape")
            assert not reason.startswith("invalid_open_event_shape")
            assert not reason.startswith("invalid_arc_shape")
            assert reason != "invalid_contract_shape"
            assert reason != "invalid_evidence_span"
    assert set(FIXTURE["baseline"]["rejection_distribution"]) == set(
        GOLD_BASELINE["rejection_distribution"]
    )


def _outline_with_section(value, section):
    if value is None:
        return None
    outline = dict(value)
    outline["_section"] = section
    return outline


def test_generated_text_integrity():
    for item in FIXTURE["subsections"]:
        manifest = {m["source_type"]: m for m in item["source_manifest"]}
        assert (
            sha256_text(item["generated_text"])
            == manifest["generated_subsection"]["source_hash"]
        )


def test_outline_text_hash_chain():
    section = FIXTURE["section"]
    for item in FIXTURE["subsections"]:
        manifest = {m["source_type"]: m for m in item["source_manifest"]}
        current = _outline_with_section(item["current_outline"], section)
        assert (
            sha256_text(_outline_text(current))
            == manifest["current_outline"]["source_hash"]
        )
        if item["next_outline"] is not None:
            following = _outline_with_section(item["next_outline"], section)
            assert (
                sha256_text(_outline_text(following))
                == manifest["next_outline"]["source_hash"]
            )
        else:
            assert "next_outline" not in manifest
            assert item["subsection"] == 4


def test_registry_rebuild_matches_frozen_hash():
    """核心回归门：registry 构建全链确定性重现真实运行。"""
    section = FIXTURE["section"]
    for item in FIXTURE["subsections"]:
        sources = build_handover_sources(
            section=section,
            subsection=item["subsection"],
            generated_text=item["generated_text"],
            current_outline=_outline_with_section(item["current_outline"], section),
            next_outline=_outline_with_section(item["next_outline"], section),
            arc_milestones=(),
        )
        registry = build_compact_source_registry(sources, arc_milestones=())
        assert registry.registry_hash == item["hashes"]["source_registry_hash"]
        rebuilt_manifest = [entry.source.public_manifest() for entry in registry.entries]
        assert rebuilt_manifest == item["source_manifest"]


def test_boundary_rebuild_matches_frozen_hash():
    section = FIXTURE["section"]
    for item in FIXTURE["subsections"]:
        boundary = compile_next_boundary(
            section=section,
            subsection=item["subsection"],
            current_outline=_outline_with_section(item["current_outline"], section),
            next_outline=_outline_with_section(item["next_outline"], section),
        )
        assert (
            sha256_json(boundary.model_dump(mode="json"))
            == item["hashes"]["next_boundary_hash"]
        )


def test_note_projection_matches_frozen_hash():
    for item in FIXTURE["subsections"]:
        note = dict(item["note_fields"])
        note["resolved_events"] = []
        assert sha256_json(note) == item["hashes"]["handover_note_hash"]
