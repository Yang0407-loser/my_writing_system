"""Enrich the generated reports with per-subsection warnings + manual review.

This is a post-hoc reporting step only: it re-runs the deterministic
NarrativeRealityChecker over the already-generated A/B texts, records the
manual review checklist, and rewrites the JSON/MD reports. It does not call the
LLM, does not regenerate the B draft, and does not modify the contract.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from app.writing.narrative_reality_checks import NarrativeRealityChecker  # noqa: E402
from experiments.scene_reality_contract_v0 import runner  # noqa: E402

BASELINE = BASE / "experiments/scene_reality_contract_v0/fixtures/baseline_draft.txt"
OUTPUT = BASE / "reports/scene-reality-contract-v0-output-2026-08-02.txt"
JSON = BASE / "reports/scene-reality-contract-v0-2026-08-02.json"
MD = BASE / "reports/scene-reality-contract-v0-2026-08-02.md"

ALLOWED = ["林晚", "周野", "顾衍", "季晴", "吴阿姨"]
KNOWN = "野面包位于林晚居住的老小区附近，只在周六营业，周野周六凌晨三点半开始揉面。"

MANUAL_REVIEW = {
    "location_stable": "pass",
    "guomao_to_old_district_transition": "pass",
    "resignation_status_matches_delivery": "pass",
    "jiqing_information_provenance": "not_applicable_beat_removed",
    "kneading_before_0330": "pass",
    "photography_permission_before_shooting": "pass",
    "batch_source_for_finished_bread": "not_applicable_scene_removed",
    "manual_explanation_inserted": "pass",
    "temporary_operations_added": "pass",
    "core_events_preserved": "fail",
    "ai_flavor_summary_added": "pass",
    "notes": [
        "小节4 核心事件改变：'凌晨三点半的陌生人'（失眠顾客）被移除，B稿改为林晚独自进入面包店，未满足大纲要点'失眠顾客偶遇面包店'。",
        "剩余3条 warning 经人工核查均为检查器误报：closed_business_activity_without_cause 在周六营业日因跨小节历史（小节1 周三店门关着）污染触发；activity_before_established_schedule 由'一点微咸'被解析为凌晨1点触发。",
        "小节3 周六序号由第三个改为第四个，属次要偏离，核心写作事件保留。",
        "季晴消息节拍被移除，导致信息传播类问题消失，但同时也丢失一个既有情节节拍。",
    ],
}


def per_subsection_warnings(text: str, splitter) -> list[dict]:
    checker = NarrativeRealityChecker(allowed_names=ALLOWED)
    sections = splitter(text)
    out = []
    for index, section_text in enumerate(sections, 1):
        record = checker.observe(
            section_text, section=1, subsection=index, known_context=KNOWN
        )
        out.append(
            {
                "subsection": index,
                "warning_codes": [w["code"] for w in record.get("warnings", [])],
                "evidence": [w["evidence"] for w in record.get("warnings", [])],
            }
        )
    return out


def main() -> int:
    baseline = BASELINE.read_text(encoding="utf-8")
    b_output = OUTPUT.read_text(encoding="utf-8")
    report = json.loads(JSON.read_text(encoding="utf-8"))

    baseline_warnings = per_subsection_warnings(baseline, runner.split_baseline_sections)
    candidate_warnings = per_subsection_warnings(b_output, runner.split_baseline_sections)

    report["baseline_subsection_warnings"] = baseline_warnings
    report["candidate_subsection_warnings"] = candidate_warnings
    report["manual_review"] = MANUAL_REVIEW
    report["manual_review_notes"] = "\n".join(MANUAL_REVIEW["notes"])

    JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    MD.write_text(_render_md(report), encoding="utf-8")
    print("reports finalized")
    return 0


def _render_md(report: dict) -> str:
    lines = [
        "# Scene Reality Contract v0 — 实验结果",
        "",
        f"- 版本: {report['version']}",
        f"- 模型: {report['model']}",
        f"- 生成调用次数: {report['generation_calls']}",
        f"- 修订调用次数: {report['revision_calls']}",
        f"- 合同哈希: {report['contract_hash']}",
        f"- Prompt 快照哈希: {report['prompt_hash']}",
        "",
        f"- A 稿 warning 总数: {report['baseline_warning_count']}",
        f"- B 稿 warning 总数: {report['candidate_warning_count']}",
        f"- 消除的原始问题数: {report['resolved_issue_count']} / 7",
        f"- 新增 warning code: {', '.join(report['new_warning_codes']) or '无'}",
        f"- 自动门槛: {'通过' if report['automatic_criteria_pass'] else '未通过'}",
        f"- 晋级状态: {report['promotion_status']}",
        f"- 生产影响: {report['production_effect']}",
        "",
        "## A 稿各小节 warning",
        "",
    ]
    for item in report["baseline_subsection_warnings"]:
        codes = ", ".join(item["warning_codes"]) or "无"
        lines.append(f"- 小节 {item['subsection']}: {codes}")
    lines.append("")
    lines.append("## B 稿各小节 warning")
    lines.append("")
    for item in report["candidate_subsection_warnings"]:
        codes = ", ".join(item["warning_codes"]) or "无"
        lines.append(f"- 小节 {item['subsection']}: {codes}")
    lines.append("")
    lines.append("## 目标字数偏差")
    lines.append("")
    for item in report["target_word_deviation"]:
        lines.append(
            f"- 小节 {item['subsection']}: {item['actual_chars']}/{item['target_words']} "
            f"(偏差 {item['deviation_ratio']:+.1%})"
        )
    lines.append("")
    lines.append("## 合同合规（自动初步判定）")
    lines.append("")
    lines.append(f"- 总评: {report['contract_compliance']['overall']}")
    for category, status in report["contract_compliance"]["categories"].items():
        lines.append(f"- {category}: {status}")
    lines.append("")
    lines.append("## 人工检查清单")
    lines.append("")
    checklist = [
        ("1. 野面包的位置是否始终稳定", report["manual_review"]["location_stable"]),
        ("2. 国贸到老小区是否有明确过渡", report["manual_review"]["guomao_to_old_district_transition"]),
        ("3. 林晚的辞职状态是否符合实际送达行为", report["manual_review"]["resignation_status_matches_delivery"]),
        ("4. 季晴的信息是否有传播来源", report["manual_review"]["jiqing_information_provenance"]),
        ("5. 三点半以前是否出现揉面", report["manual_review"]["kneading_before_0330"]),
        ("6. 拍摄周野前是否获得许可", report["manual_review"]["photography_permission_before_shooting"]),
        ("7. 三点四十分的成品是否有合法批次来源", report["manual_review"]["batch_source_for_finished_bread"]),
        ("8. 是否为了遵守合同而增加说明书式解释", report["manual_review"]["manual_explanation_inserted"]),
        ("9. 是否新增临时经营设定", report["manual_review"]["temporary_operations_added"]),
        ("10. 是否改变原有四个小节的核心事件", report["manual_review"]["core_events_preserved"]),
        ("11. 是否明显增加 DeepSeek 式比喻/总结/主题升华", report["manual_review"]["ai_flavor_summary_added"]),
    ]
    status_map = {"pass": "通过", "fail": "不通过"}
    for label, value in checklist:
        lines.append(f"- {label}: {status_map.get(value, value)}")
    lines.append("")
    lines.append("## 人工核验备注")
    lines.append("")
    for note in report["manual_review"]["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    lines.append(
        "- 自动门槛：未通过（7 类原始问题仅消除 5 类，需消除 ≥6 类；剩余 3 条 warning 经人工核查为检查器误报）。"
    )
    lines.append(
        "- 小节 4 核心事件改变（失眠顾客被移除），不满足通过标准。"
    )
    lines.append("- 结论：实验失败，不接入生产；不重跑，不修改合同。")
    lines.append("- 最终晋级需人工盲审确认，当前 promotion_status=failed。")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
