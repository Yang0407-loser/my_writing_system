from experiments.reality_repair_probe.cli import split_generated_sections
from experiments.reality_repair_probe.runner import (
    build_repair_instruction,
    run_repair_probe,
)


def test_instruction_forbids_broad_style_rewrite():
    instruction = build_repair_instruction({
        "warnings": [{
            "code": "activity_before_established_schedule",
            "message": "活动早于时间表。",
            "evidence": "凌晨三点，揉面声已经响起。",
        }]
    })

    assert "不得润色全文" in instruction
    assert "最少句子" in instruction
    assert "activity_before_established_schedule" in instruction


def test_probe_revises_warned_section_once_and_preserves_clean_section():
    sections = [
        "凌晨三点。门里已经传来揉面的声音。",
        "凌晨四点，她沿着街道回家。",
    ]
    calls = []

    def revise(original, instruction):
        calls.append((original, instruction))
        return original.replace("三点。", "三点半。")

    result = run_repair_probe(
        sections,
        revise=revise,
        known_context="凌晨三点半开始揉面。",
    )

    assert len(calls) == 1
    assert result["revision_calls"] == 1
    assert result["original_warning_count"] == 1
    assert result["resolved_warning_count"] == 1
    assert result["new_warning_count"] == 0
    assert result["automatic_criteria_pass"] is True
    assert result["promotion_status"] == "pending_human_review"
    assert result["cases"][1]["original"] == result["cases"][1]["revised"]


def test_probe_fails_when_revision_introduces_new_warning():
    def revise(_original, _instruction):
        return "凌晨三点半，她到了。那个名字——程明——她念了一遍。"

    result = run_repair_probe(
        ["凌晨三点。门里已经传来揉面的声音。"],
        revise=revise,
        known_context="凌晨三点半开始揉面。",
    )

    assert result["new_warning_count"] == 1
    assert result["automatic_criteria_pass"] is False
    assert result["promotion_status"] == "failed"


def test_split_generated_sections_preserves_first_section_prefix():
    text = (
        "第1节：第一卷\n"
        "第1章：开端\n10/20\n正文一\n"
        "三次蹲守\n15/20\n正文二"
    )

    sections = split_generated_sections(text)

    assert len(sections) == 2
    assert sections[0].startswith("第1节：第一卷\n第1章：开端")
    assert sections[1].startswith("三次蹲守\n15/20")
