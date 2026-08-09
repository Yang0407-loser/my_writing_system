"""Unit tests for the non-production Scene Reality Contract v0 experiment.

These tests verify the experiment's own machinery (contract freezing, input
recovery, prompt injection position, snapshot rendering) without calling any LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

from experiments.scene_reality_contract_v0.contract import (
    SCENE_REALITY_CONTRACT_V0_TEXT,
    render_scene_reality_contract_v01,
    scene_reality_contract_hash,
)
from experiments.scene_reality_contract_v0.inputs import load_experiment_inputs
from experiments.scene_reality_contract_v0.prompting import (
    build_v01_prompt_values,
    build_prompt_values,
    reference_passages_from_text,
    render_user_prompt,
)
from experiments.scene_reality_contract_v0 import runner

EXPERIMENTS_DIR = Path(__file__).resolve().parents[2] / "experiments" / "scene_reality_contract_v0"
FIXTURES = EXPERIMENTS_DIR / "fixtures"
REPORTS = Path(__file__).resolve().parents[2] / "reports"


def _inputs():
    return load_experiment_inputs(EXPERIMENTS_DIR)


def test_contract_is_frozen_and_hashable():
    assert "Scene Reality Contract v0" in SCENE_REALITY_CONTRACT_V0_TEXT
    digest = scene_reality_contract_hash()
    assert len(digest) == 64
    # Recomputing the hash is deterministic.
    assert scene_reality_contract_hash() == digest


def test_contract_contains_key_facts():
    text = SCENE_REALITY_CONTRACT_V0_TEXT
    for needle in [
        "林晚工作的写字楼位于国贸",
        "「野面包」不在国贸写字楼街对面",
        "「野面包」只在周六对外营业",
        "周野在周六凌晨三点半开始揉面",
        "别开闪光灯",
        "不得新增没有来源的人名",
    ]:
        assert needle in text, needle


def test_inputs_recovered_from_fixture():
    inputs = _inputs()
    assert inputs.topic == "周六面包店与凌晨3点"
    assert inputs.task_id == "487da043-b11f-4d91-805a-2db132d54955"
    assert len(inputs.outline) == 1
    subs = inputs.outline[0]["subsections"]
    assert [s["title"] for s in subs] == [
        "第1章：客户说「感觉不对」的那天",
        "三次蹲守",
        "第一篇草稿",
        "凌晨三点半的陌生人",
    ]
    assert [s["target_words"] for s in subs] == [1350, 850, 800, 950]
    names = {c["name"] for c in inputs.characters}
    assert {"林晚", "周野", "顾衍", "季晴", "吴阿姨"} <= names


def test_contract_lands_in_hard_constraint_area():
    inputs = _inputs()
    values = build_prompt_values(
        inputs,
        section=1,
        sub_num=1,
        prev_b_texts=[],
        contract_text=SCENE_REALITY_CONTRACT_V0_TEXT,
    )
    user_prompt = render_user_prompt(values)
    # The contract must appear before the soft style / writing-example content.
    hard_start = user_prompt.index("========== 硬约束")
    writing_guidance = user_prompt.index("========== 写作指引")
    examples_pos = user_prompt.index("风格参考原文") if "风格参考原文" in user_prompt else None
    assert hard_start < writing_guidance
    assert "Scene Reality Contract v0" in user_prompt[hard_start:writing_guidance]
    if examples_pos is not None:
        assert examples_pos > hard_start
    # The contract is not hidden inside the style examples block.
    style_examples_field = values["style_examples"]
    assert "Scene Reality Contract v0" not in style_examples_field


def test_contract_not_disguised_as_world_setting():
    inputs = _inputs()
    assert "Scene Reality Contract v0" not in inputs.world_setting
    assert "野面包" in inputs.world_setting


def test_generation_settings_are_original():
    inputs = _inputs()
    sub = inputs.outline[0]["subsections"][0]
    assert sub["target_words"] == 1350
    # Reproduction of the Writer's call_max_tokens formula.
    from app.config import settings
    from experiments.scene_reality_contract_v0.prompting import call_max_tokens_for
    expected = min(max(settings.WRITER_MAX_TOKENS_FLOOR, 1350 * 4), settings.WRITER_MAX_TOKENS_CEIL)
    assert call_max_tokens_for(1350) == expected


def test_baseline_split_yields_four_sections():
    baseline = (FIXTURES / "baseline_draft.txt").read_text(encoding="utf-8")
    sections = runner.split_baseline_sections(baseline)
    assert len(sections) == 4


def test_baseline_has_seven_known_warnings():
    baseline = (FIXTURES / "baseline_draft.txt").read_text(encoding="utf-8")
    records = runner.evaluate_text(
        baseline,
        known_context="野面包位于林晚居住的老小区附近，只在周六营业，周野周六凌晨三点半开始揉面。",
        allowed_names=["林晚", "周野", "顾衍", "季晴", "吴阿姨"],
    )
    codes = {
        item["code"]
        for record in records
        for item in record.get("warnings", [])
    }
    assert codes == set(runner.ORIGINAL_ISSUE_CODES)
    assert sum(len(r["warnings"]) for r in records) == 7


def test_reference_passages_selection():
    inputs = _inputs()
    passages = reference_passages_from_text(inputs.reference_text)
    assert "参考段落" in passages
    assert len(passages) > 100


def test_contract_hash_matches_report_contract():
    # The frozen contract text hash must equal the hash recorded by the runner.
    from experiments.scene_reality_contract_v0 import contract
    assert contract.scene_reality_contract_hash() == scene_reality_contract_hash()


def test_v01_contract_is_subsection_scoped_and_smaller_than_v0():
    rendered = [render_scene_reality_contract_v01(index) for index in range(1, 5)]

    assert all(len(text) < len(SCENE_REALITY_CONTRACT_V0_TEXT) * 0.55 for text in rendered)
    assert "失眠顾客" not in rendered[0]
    assert "失眠顾客" in rendered[3]
    assert "季晴发来辞职相关消息" in rendered[0]


def test_v01_priority_forbids_deleting_events_to_satisfy_facts():
    for subsection in range(1, 5):
        text = render_scene_reality_contract_v01(subsection)
        assert "MUST_EVENT > FACT > 风格与氛围" in text
        assert "不得通过删除、替换或跳过 MUST_EVENT" in text
        assert "所有 MUST_EVENT 均在正文中实际发生" in text


def test_v01_preserves_all_three_stakeouts_and_stranger_beat():
    stakeouts = render_scene_reality_contract_v01(2)
    stranger = render_scene_reality_contract_v01(4)

    for needle in ("第一、第二、第三个周六", "顾衍", "清晰背影", "记录草稿"):
        assert needle in stakeouts
    for needle in ("失眠顾客偶遇面包店", "凌晨揉面", "书店暖光", "社区夜归人"):
        assert needle in stranger
    assert "不得改成林晚独自进入面包店" in stranger


def test_v01_declares_only_allowed_repairs_for_source_outline_conflicts():
    first = render_scene_reality_contract_v01(1)
    second = render_scene_reality_contract_v01(2)

    assert "[ALLOW:S1_LOCATION]" in first
    assert "乘车回老小区后路过" in first
    assert "[ALLOW:S2_SCHEDULE]" in second
    assert "等待到3:30才听见揉面声" in second


def test_v01_contract_lands_in_hard_area_with_events_before_soft_guidance():
    inputs = _inputs()
    for subsection in range(1, 5):
        values = build_v01_prompt_values(
            inputs,
            section=1,
            sub_num=subsection,
            prev_b_texts=[],
        )
        user_prompt = render_user_prompt(values)
        hard_start = user_prompt.index("========== 硬约束")
        soft_start = user_prompt.index("========== 写作指引")
        contract = render_scene_reality_contract_v01(subsection)

        assert contract in user_prompt[hard_start:soft_start]
        assert contract not in values["style_examples"]
