"""CLI for the non-production Scene Reality Contract v0.1 experiment.

This is an independent experiment; it never touches the production Writer, never
overwrites the old v0 report files, and makes exactly one generation call per
subsection with zero revision calls. The v0.1 contract is subsection-scoped and
must be injected via ``build_v01_prompt_values`` (which renders
``render_scene_reality_contract_v01``); the old global ``SCENE_REALITY_CONTRACT_V0_TEXT``
is never sent to the model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from app.config import settings
from app.utils.llm_client import get_llm_client
from app.utils.word_counter import count_chinese_chars
from app.writing.narrative_reality_checks import NarrativeRealityChecker

from . import runner
from .contract import (
    SCENE_REALITY_CONTRACT_V01_VERSION,
    SCENE_REALITY_CONTRACT_V0_TEXT,
    render_scene_reality_contract_v01,
    scene_reality_contract_v01_hash,
)
from .inputs import load_experiment_inputs
from .prompting import (
    build_prompt_artifact,
    build_v01_prompt_values,
    call_max_tokens_for,
)

EXPERIMENT_VERSION = "scene-reality-contract-v0.1"
TASK_ID = "scene-reality-contract-v01-2026-08-02"

# The seven original problem categories present in the baseline A draft.
ORIGINAL_ISSUE_CODES = list(runner.ORIGINAL_ISSUE_CODES)

_CONTRACT_WARNING_MAP = {
    "location": {"location_anchor_conflict"},
    "resignation_status": {"institutional_action_marked_complete_without_delivery"},
    "information_provenance": {"knowledge_without_transmission_path"},
    "business_hours": {
        "closed_business_activity_without_cause",
        "activity_before_established_schedule",
    },
    "photography_permission": {"recording_without_explicit_permission"},
    "process_duration": {"process_duration_without_prior_batch"},
}

# Per-subsection MUST_EVENT checks. Each entry maps a named invariant (section 九
# of the experiment spec) to a list of strings that must all appear in the text.
# The conditional S4 batch-source check is handled separately because it only
# fires when a finished batch appears at ~03:40.
MANDATORY_EVENT_CHECKS = {
    1: [
        ("received_20th_rejection_email", ["第20版", "驳回"]),
        ("resignation_letter_to_private_mail", ["辞职信", "私人邮箱"]),
        ("rode_from_guomao_to_old_district", ["乘车", "老小区"]),
        ("wild_bread_clue_near_old_district", ["野面包", "老小区"]),
        ("jiqing_resignation_message", ["季晴", "辞职"]),
        ("blackboard_photo_retrieved", ["小黑板"]),
        ("first_life_slice_decision", ["生活切片"]),
    ],
    2: [
        ("three_saturdays_stakeouts", ["第一个周六", "第二个周六", "第三个周六"]),
        ("first_arrival_waits_until_0330", ["三点半"]),
        ("first_sound_only_no_visible_person", ["揉面声"]),
        ("second_guided_by_guyan", ["顾衍"]),
        ("second_blurry_unidentifiable_back", ["模糊"]),
        ("third_permission_before_clear_shot", ["清晰"]),
        ("record_draft_written", ["记录"]),
    ],
    3: [
        ("no_flash_permission_boundary", ["别开闪光灯"]),
        ("zhouye_continues_kneading", ["揉面"]),
        ("linwan_exits_store", ["退出", "退后半步"]),
        ("sits_on_night_boat_steps", ["夜航船", "台阶"]),
        ("writes_first_record_article", ["一个只肯把时间分给面包的人"]),
        ("focus_and_reflection_details", ["案板", "不想成为"]),
    ],
    4: [
        ("insomniac_customer", ["失眠", "睡不着的男人"]),
        ("customer_intersects_with_zhouye", ["周野"]),
        ("dawn_kneading_ritual", ["揉面"]),
        ("bookstore_warm_light", ["夜航船", "暖"]),
        ("community_night_returner", ["吴阿姨"]),
        ("not_only_linwan_in_bakery", ["顾客", "男人"]),
    ],
}

# 说明书口吻 / AI 式总结·升华·模板化比喻的判别模式（保守清单）。
MANUAL_TONE_PATTERNS = [
    r"本小节",
    r"本合同",
    r"如上所述",
    r"综上所述",
    r"总而言之",
    r"由此可见",
    r"需要注意",
    r"特别说明",
    r"值得注意",
    r"需要指出",
    r"必须说明",
    r"（注[:：]",
    r"说明[:：]",
]
AI_FLAVOR_PATTERNS = [
    r"人生就像",
    r"人生如",
    r"生活总是",
    r"生活就是",
    r"或许这就是",
    r"也许这就是",
    r"这大概就是",
    r"这一切都",
    r"一切终将",
    r"时间会证明",
    r"命运的安排",
    r"生命的意义",
    r"新的开始",
    r"重新出发",
    r"踏上新的旅程",
    r"未来可期",
    r"星光不负",
    r"仿佛置身",
    r"宛如梦境",
    r"如诗如画",
    r"美得令人窒息",
    r"突然明白",
    r"猛然醒悟",
]
_TEMPORARY_OPERATION_PATTERNS = [
    r"试做新品",
    r"临时订单",
    r"店主加班",
    r"临时生产",
    r"加班生产",
    r"临时加烤",
    r"补一炉",
]


def _warning_codes(records: list[dict]) -> Counter:
    return Counter(
        item["code"] for record in records for item in record.get("warnings") or []
    )


def evaluate_text(
    text: str, *, known_context: str, allowed_names: list[str]
) -> list[dict]:
    checker = NarrativeRealityChecker(allowed_names=allowed_names)
    records = []
    for index, section_text in enumerate(runner.split_baseline_sections(text), 1):
        record = checker.observe(
            section_text, section=1, subsection=index, known_context=known_context
        )
        if record is not None:
            records.append(record)
    return records


def _section_texts(sections: list[dict]) -> list[str]:
    return [section["text"] for section in sections]


def check_mandatory_events(sections: list[dict]) -> dict:
    """Return {subsection: {label: bool}} for every MUST_EVENT check."""
    result: dict[int, dict] = {}
    for section in sections:
        sub_num = int(section["subsection"])
        text = section["text"]
        result[sub_num] = {}
        for label, needles in MANDATORY_EVENT_CHECKS[sub_num]:
            result[sub_num][label] = all(needle in text for needle in needles)
        if sub_num == 4:
            # Conditional: a finished batch at ~03:40 needs a prior-batch source.
            finished = re.search(r"(?:从烤箱里|出炉|端出)", text)
            if finished:
                batch_source = any(
                    needle in text for needle in ("前一晚", "冷藏发酵", "上一批", "另一批", "提前发好", "已经发好")
                )
                result[sub_num]["batch_source_when_finished"] = batch_source
            else:
                result[sub_num]["batch_source_when_finished"] = True
    return result


def mandatory_event_summary(compliance: dict) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for sub_num, checks in compliance.items():
        for label, ok in checks.items():
            if not ok:
                missing.append(f"S{sub_num}:{label}")
    return (not missing, missing)


def detect_tone_flags(text: str) -> dict:
    manual = sorted(
        {match for pattern in MANUAL_TONE_PATTERNS for match in re.findall(pattern, text)}
    )
    ai = sorted(
        {match for pattern in AI_FLAVOR_PATTERNS for match in re.findall(pattern, text)}
    )
    return {"manual_tone": manual, "ai_flavor": ai}


def detect_temporary_operations(text: str) -> list[str]:
    return sorted(
        {match for pattern in _TEMPORARY_OPERATION_PATTERNS for match in re.findall(pattern, text)}
    )


def _render_b_draft(sections: list[dict]) -> str:
    lines = []
    for section in sections:
        lines.append(f"第{section['subsection']}小节：{section['title']}")
        lines.append(f"{section['char_count']}/{section['target_words']}")
        lines.append("")
        lines.append(section["text"])
        lines.append("")
    return "\n".join(lines)


def build_all_prompts(
    inputs, task_id: str, prev_b_texts: list[str] | None = None
) -> list[dict]:
    prompts = []
    chained = list(prev_b_texts) if prev_b_texts else []
    sec = inputs.sections[0]
    for sub in sec.get("subsections", []):
        sub_num = int(sub.get("subsection", 0))
        target_words = int(sub.get("target_words", 2000))
        values = build_v01_prompt_values(
            inputs,
            section=1,
            sub_num=sub_num,
            prev_b_texts=chained,
        )
        artifact = build_prompt_artifact(
            inputs,
            values,
            section=1,
            sub_num=sub_num,
            task_id=task_id,
            target_words=target_words,
        )
        prompts.append(
            {
                "subsection": sub_num,
                "title": sub.get("title", ""),
                "target_words": target_words,
                "contract_text": render_scene_reality_contract_v01(sub_num),
                "contract_hash": scene_reality_contract_v01_hash(sub_num),
                "prompt_hash": artifact.messages_hash,
                "messages": artifact.messages,
            }
        )
        chained.append("")
    return prompts


def _prompt_snapshot_header() -> list[str]:
    return [
        "# Scene Reality Contract v0.1 — 最终发送给 Writer 的 Prompt 快照",
        f"# 版本: {EXPERIMENT_VERSION} / {SCENE_REALITY_CONTRACT_V01_VERSION}",
        f"# 模型: {settings.WRITER_LLM_MODEL}",
        "",
    ]


def _prompt_snapshot_block(prompt: dict) -> list[str]:
    block = [
        "=" * 72,
        f"## 小节 {prompt['subsection']}: {prompt['title']}",
        f"## target_words: {prompt['target_words']}",
        f"## 小节合同哈希: {prompt['contract_hash']}",
        f"## 小节 prompt_hash: {prompt['prompt_hash']}",
        "=" * 72,
    ]
    for message in prompt["messages"]:
        block.append(f"### role: {message['role']}")
        block.append(message["content"])
        block.append("")
    return block


def render_snapshot(prompts: list[dict]) -> str:
    lines = _prompt_snapshot_header()
    for prompt in prompts:
        lines.extend(_prompt_snapshot_block(prompt))
    return "\n".join(lines)


def append_snapshot(path: Path, prompt: dict, *, first: bool) -> None:
    lines = _prompt_snapshot_header() if first else []
    lines.extend(_prompt_snapshot_block(prompt))
    payload = "\n".join(lines) + "\n"
    if first:
        path.write_text(payload, encoding="utf-8")
    else:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(payload)


def verify_dry_run(prompts: list[dict]) -> dict:
    checks: dict[str, object] = {}
    checks["four_prompts"] = len(prompts) == 4
    checks["subsection_numbers"] = [p["subsection"] for p in prompts] == [1, 2, 3, 4]

    contract_occurrences = [
        sum(message["content"].count("Scene Reality Contract v0.1") for message in p["messages"])
        for p in prompts
    ]
    checks["contract_once_per_prompt"] = contract_occurrences == [1, 1, 1, 1]

    hard_positions = []
    soft_positions = []
    style_integrity_ok = True
    for p in prompts:
        user_prompt = p["messages"][1]["content"]
        hard_start = user_prompt.find("========== 硬约束")
        soft_start = user_prompt.find("========== 写作指引")
        style_start = user_prompt.find("风格参考原文")
        hard_positions.append(hard_start)
        soft_positions.append(soft_start)
        if hard_start == -1 or soft_start == -1:
            style_integrity_ok = False
            continue
        if p["contract_text"] not in user_prompt[hard_start:soft_start]:
            style_integrity_ok = False
        if style_start != -1 and style_start <= hard_start:
            style_integrity_ok = False
    checks["contract_in_hard_constraint_area"] = style_integrity_ok
    checks["contract_before_writing_guidance"] = all(
        h < s for h, s in zip(hard_positions, soft_positions)
    )

    style_examples_ok = True
    for p in prompts:
        for message in p["messages"]:
            if message["role"] == "user" and "风格参考原文" in message["content"]:
                # The contract is never part of the style-example field; verify the
                # field value itself does not contain the contract header.
                seg = message["content"]
                after = seg[seg.find("风格参考原文"):]
                if "Scene Reality Contract v0.1" in after:
                    style_examples_ok = False
    checks["contract_not_in_style_examples"] = style_examples_ok

    s1 = prompts[0]["contract_text"]
    checks["s1_jiqing_and_allows"] = (
        "季晴发来辞职相关消息" in s1
        and "ALLOW:S1_LOCATION" in s1
        and "ALLOW:S1_KNOWLEDGE" in s1
    )
    s2 = prompts[1]["contract_text"]
    checks["s2_three_saturdays_and_allows"] = (
        "第一、第二、第三个周六" in s2
        and "ALLOW:S2_SCHEDULE" in s2
        and "ALLOW:S2_PERMISSION" in s2
    )
    s3 = prompts[2]["contract_text"]
    checks["s3_first_record_and_allow"] = (
        "第一篇记录" in s3 and "ALLOW:S3_STATUS" in s3
    )
    s4 = prompts[3]["contract_text"]
    checks["s4_stranger_light_returner_and_allow"] = (
        "失眠顾客偶遇面包店" in s4
        and "书店暖光" in s4
        and "社区夜归人" in s4
        and "ALLOW:S4_BATCH" in s4
        and "不得改成林晚独自进入面包店" in s4
    )

    checks["no_old_global_v0_contract"] = all(
        SCENE_REALITY_CONTRACT_V0_TEXT not in p["contract_text"] for p in prompts
    ) and all(
        "Scene Reality Contract v0\n" not in message["content"]
        for p in prompts
        for message in p["messages"]
    )

    expected_lengths = {1: 548, 2: 517, 3: 412, 4: 420}
    length_results = {
        p["subsection"]: {
            "actual": len(p["contract_text"]),
            "expected": expected_lengths[p["subsection"]],
        }
        for p in prompts
    }
    checks["contract_lengths"] = length_results
    checks["contract_lengths_match"] = all(
        len(p["contract_text"]) == expected_lengths[p["subsection"]]
        for p in prompts
    )

    checks["subsection_contract_hashes"] = {
        p["subsection"]: p["contract_hash"] for p in prompts
    }
    checks["four_distinct_contract_hashes"] = (
        len({p["contract_hash"] for p in prompts}) == 4
    )

    checks["all_pass"] = all(
        isinstance(v, bool) and v for k, v in checks.items() if k.endswith("_pass")
        or k in {
            "four_prompts", "subsection_numbers", "contract_once_per_prompt",
            "contract_in_hard_constraint_area", "contract_before_writing_guidance",
            "contract_not_in_style_examples", "s1_jiqing_and_allows",
            "s2_three_saturdays_and_allows", "s3_first_record_and_allow",
            "s4_stranger_light_returner_and_allow", "no_old_global_v0_contract",
            "contract_lengths_match", "four_distinct_contract_hashes",
        }
    )
    return checks


def run_v01_experiment(
    *,
    baseline_text: str,
    generate,
    inputs,
    task_id: str = TASK_ID,
    reports_dir: Path | None = None,
) -> dict:
    reports_dir = reports_dir or Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    prompt_snapshot_path = (
        reports_dir / "scene-reality-contract-v01-prompt-2026-08-02.txt"
    )

    allowed_names = list(inputs.allowed_names)
    known_context = inputs.known_context

    baseline_records = evaluate_text(
        baseline_text, known_context=known_context, allowed_names=allowed_names
    )
    baseline_codes = _warning_codes(baseline_records)

    prompts: list[dict] = []
    sections: list[dict] = []
    prev_b_texts: list[str] = []
    generation_calls = 0

    sec = inputs.sections[0]
    for sub in sec.get("subsections", []):
        sub_num = int(sub.get("subsection", 0))
        target_words = int(sub.get("target_words", 2000))
        values = build_v01_prompt_values(
            inputs,
            section=1,
            sub_num=sub_num,
            prev_b_texts=prev_b_texts,
        )
        artifact = build_prompt_artifact(
            inputs,
            values,
            section=1,
            sub_num=sub_num,
            task_id=task_id,
            target_words=target_words,
        )
        prompt_entry = {
            "subsection": sub_num,
            "title": sub.get("title", ""),
            "target_words": target_words,
            "contract_text": render_scene_reality_contract_v01(sub_num),
            "contract_hash": scene_reality_contract_v01_hash(sub_num),
            "prompt_hash": artifact.messages_hash,
            "messages": artifact.messages,
        }
        prompts.append(prompt_entry)
        append_snapshot(prompt_snapshot_path, prompt_entry, first=sub_num == 1)

        call_max = call_max_tokens_for(target_words)
        raw = generate(artifact.messages, call_max)
        generation_calls += 1
        text = (raw or "").strip()
        sections.append(
            {
                "subsection": sub_num,
                "title": sub.get("title", ""),
                "target_words": target_words,
                "text": text,
                "char_count": count_chinese_chars(text),
            }
        )
        prev_b_texts.append(text)

    b_text = _render_b_draft(sections)
    candidate_records = evaluate_text(
        b_text, known_context=known_context, allowed_names=allowed_names
    )
    candidate_codes = _warning_codes(candidate_records)

    resolved = [
        code
        for code in ORIGINAL_ISSUE_CODES
        if baseline_codes.get(code, 0) > 0 and candidate_codes.get(code, 0) == 0
    ]
    new_codes = [
        code for code in candidate_codes if baseline_codes.get(code, 0) == 0
    ]

    prompt_snapshot = prompt_snapshot_path.read_text(encoding="utf-8")
    prompt_hash = hashlib.sha256(prompt_snapshot.encode("utf-8")).hexdigest()

    target_deviation = runner.compute_target_deviation(sections)
    contract_compliance = _automatic_contract_compliance(candidate_codes)
    mandatory_compliance = check_mandatory_events(sections)
    mandatory_ok, missing_events = mandatory_event_summary(mandatory_compliance)

    tone_flags = {
        sub_num: detect_tone_flags(section["text"])
        for sub_num, section in ((s["subsection"], s) for s in sections)
    }
    temp_ops = {
        sub_num: detect_temporary_operations(section["text"])
        for sub_num, section in ((s["subsection"], s) for s in sections)
    }
    forbidden_inventions = {
        "unsupported_named_entity": "unsupported_named_entity" in candidate_codes,
        "temporary_operations": {str(k): v for k, v in temp_ops.items()},
        "all_clean": (
            "unsupported_named_entity" not in candidate_codes
            and not any(temp_ops.values())
        ),
    }

    manual_tone_clean = all(not v["manual_tone"] for v in tone_flags.values())
    ai_flavor_clean = all(not v["ai_flavor"] for v in tone_flags.values())

    revision_calls = 0
    deviation_ok = all(
        item["deviation_ratio"] is not None and abs(item["deviation_ratio"]) <= 0.30
        for item in target_deviation
    )

    contract_category_ok = all(
        status == "pass"
        for status in contract_compliance["categories"].values()
        if status != "needs_review"
    )

    automatic_criteria_pass = (
        settings.WRITER_LLM_MODEL == "deepseek-v4-flash"
        and generation_calls == 4
        and revision_calls == 0
        and candidate_codes.total() == 0
        and not new_codes
        and mandatory_ok
        and forbidden_inventions["all_clean"]
        and deviation_ok
        and manual_tone_clean
        and ai_flavor_clean
        and contract_category_ok
    )
    promotion_status = (
        "pending_human_review" if automatic_criteria_pass else "failed"
    )

    result = {
        "version": EXPERIMENT_VERSION,
        "model": settings.WRITER_LLM_MODEL,
        "generation_calls": generation_calls,
        "revision_calls": revision_calls,
        "subsection_contract_hashes": {
            p["subsection"]: p["contract_hash"] for p in prompts
        },
        "subsection_prompt_hashes": {
            p["subsection"]: p["prompt_hash"] for p in prompts
        },
        "prompt_hash": prompt_hash,
        "baseline_warning_count": int(sum(baseline_codes.values())),
        "candidate_warning_count": int(sum(candidate_codes.values())),
        "baseline_warning_codes": dict(sorted(baseline_codes.items())),
        "candidate_warning_codes": dict(sorted(candidate_codes.items())),
        "resolved_issue_count": len(resolved),
        "resolved_issue_codes": resolved,
        "new_warning_codes": new_codes,
        "mandatory_event_compliance": {
            str(k): v for k, v in mandatory_compliance.items()
        },
        "missing_mandatory_events": missing_events,
        "contract_compliance": contract_compliance,
        "forbidden_inventions": forbidden_inventions,
        "tone_flags": {str(k): v for k, v in tone_flags.items()},
        "target_word_deviation": target_deviation,
        "automatic_criteria_pass": automatic_criteria_pass,
        "human_review_required": True,
        "promotion_status": promotion_status,
        "production_effect": False,
        "contract_version": SCENE_REALITY_CONTRACT_V01_VERSION,
        "per_subsection": [
            {
                "subsection": section["subsection"],
                "title": section["title"],
                "target_words": section["target_words"],
                "actual_chars": section["char_count"],
                "prompt_hash": prompt["prompt_hash"],
                "contract_hash": prompt["contract_hash"],
            }
            for section, prompt in zip(sections, prompts)
        ],
        "candidate_sections": sections,
        "baseline_sections": [
            {
                "subsection": i + 1,
                "text": text,
                "warning_codes": [w["code"] for w in record.get("warnings", [])],
            }
            for i, (text, record) in enumerate(
                zip(runner.split_baseline_sections(baseline_text), baseline_records)
            )
        ],
        "candidate_records": candidate_records,
    }
    return result


def _automatic_contract_compliance(candidate_codes: Counter) -> dict:
    categories: dict[str, str] = {}
    for category, codes in _CONTRACT_WARNING_MAP.items():
        hits = sum(candidate_codes.get(code, 0) for code in codes)
        categories[category] = "warn" if hits else "pass"
    categories["forbidden_inventions"] = "needs_review"
    categories["no_unsourced_new_settings"] = "needs_review"
    category_statuses = list(categories.values())
    auto_pass = all(status == "pass" for status in category_statuses)
    return {
        "overall": "pass" if auto_pass else "needs_human_review",
        "categories": categories,
    }


def write_outputs(result: dict, reports_dir: Path, b_draft: str) -> dict:
    reports_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = reports_dir / "scene-reality-contract-v01-prompt-2026-08-02.txt"
    output_path = reports_dir / "scene-reality-contract-v01-output-2026-08-02.txt"
    json_path = reports_dir / "scene-reality-contract-v01-2026-08-02.json"
    md_path = reports_dir / "scene-reality-contract-v01-2026-08-02.md"

    output_path.write_text(b_draft, encoding="utf-8")

    json_payload = {
        key: value
        for key, value in result.items()
        if key
        not in {"candidate_sections", "baseline_sections", "candidate_records"}
    }
    json_path.write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(_render_markdown_report(result), encoding="utf-8")

    return {
        "prompt": str(prompt_path.resolve()),
        "output": str(output_path.resolve()),
        "json": str(json_path.resolve()),
        "md": str(md_path.resolve()),
    }


def _render_markdown_report(result: dict) -> str:
    lines = [
        "# Scene Reality Contract v0.1 — 实验结果",
        "",
        f"- 版本: {result['version']}",
        f"- 模型: {result['model']}",
        f"- 生成调用次数: {result['generation_calls']}",
        f"- 修订调用次数: {result['revision_calls']}",
        f"- Prompt 快照哈希: {result['prompt_hash']}",
        "",
        f"- A 稿 warning 总数: {result['baseline_warning_count']}",
        f"- B 稿 warning 总数: {result['candidate_warning_count']}",
        f"- 消除的原始问题数: {result['resolved_issue_count']} / 7",
        f"- 新增 warning code: {', '.join(result['new_warning_codes']) or '无'}",
        f"- 自动门槛: {'通过' if result['automatic_criteria_pass'] else '未通过'}",
        f"- 晋级状态: {result['promotion_status']}",
        f"- 生产影响: {result['production_effect']}",
        "",
        "## 各小节合同哈希",
        "",
    ]
    for sub_num, contract_hash in result["subsection_contract_hashes"].items():
        lines.append(f"- 小节 {sub_num}: {contract_hash}")
    lines.append("")
    lines.append("## 各小节 Prompt 哈希")
    lines.append("")
    for sub_num, prompt_hash in result["subsection_prompt_hashes"].items():
        lines.append(f"- 小节 {sub_num}: {prompt_hash}")
    lines.append("")
    lines.append("## A 稿各小节 warning")
    lines.append("")
    for item in result["baseline_sections"]:
        codes = ", ".join(item["warning_codes"]) or "无"
        lines.append(f"- 小节 {item['subsection']}: {codes}")
    lines.append("")
    lines.append("## B 稿各小节 warning")
    lines.append("")
    for record in result["candidate_records"]:
        codes = ", ".join(w["code"] for w in record.get("warnings", [])) or "无"
        lines.append(f"- 小节 {record['subsection']}: {codes}")
    lines.append("")
    lines.append("## MUST_EVENT 合规")
    lines.append("")
    for sub_num in sorted(result["mandatory_event_compliance"], key=int):
        checks = result["mandatory_event_compliance"][sub_num]
        statuses = "、".join(
            f"{label}:{'通过' if ok else '缺失'}" for label, ok in checks.items()
        )
        lines.append(f"- 小节 {sub_num}: {statuses}")
    missing = result["missing_mandatory_events"]
    lines.append(f"- 缺失事件: {', '.join(missing) or '无'}")
    lines.append("")
    lines.append("## 目标字数偏差")
    lines.append("")
    for item in result["target_word_deviation"]:
        lines.append(
            f"- 小节 {item['subsection']}: {item['actual_chars']}/{item['target_words']} "
            f"(偏差 {item['deviation_ratio']:+.1%})"
        )
    lines.append("")
    lines.append("## 合同合规（自动初步判定）")
    lines.append("")
    lines.append(f"- 总评: {result['contract_compliance']['overall']}")
    for category, status in result["contract_compliance"]["categories"].items():
        lines.append(f"- {category}: {status}")
    lines.append("")
    lines.append("## 合同外新设定 / 说明书口吻 / AI 味")
    lines.append("")
    lines.append(
        f"- 禁止新增经营设定: "
        f"{'干净' if result['forbidden_inventions']['all_clean'] else result['forbidden_inventions']}"
    )
    for sub_num in sorted(result["tone_flags"], key=int):
        flags = result["tone_flags"][sub_num]
        lines.append(
            f"- 小节 {sub_num} 说明书口吻: {', '.join(flags['manual_tone']) or '无'}；"
            f"AI 味: {', '.join(flags['ai_flavor']) or '无'}"
        )
    lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append(
        "- 本合同为冻结版本，生成前已保存完整 Prompt 快照，合同位于硬约束区域。"
    )
    lines.append("- B 稿只生成一次，无写后修订，未接入生产 Writer。")
    lines.append("- 最终晋级需人工盲审确认。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, help="A 稿原文路径")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--task-id", default=TASK_ID)
    parser.add_argument("--dry-run", action="store_true", help="只构建 Prompt 快照，不调用模型")
    args = parser.parse_args()

    baseline_text = Path(args.baseline).read_text(encoding="utf-8")
    inputs = load_experiment_inputs()
    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = reports_dir / "scene-reality-contract-v01-prompt-2026-08-02.txt"

    if args.dry_run:
        prompts = build_all_prompts(inputs, args.task_id)
        snapshot = render_snapshot(prompts)
        prompt_path.write_text(snapshot, encoding="utf-8")
        verification = verify_dry_run(prompts)

        print(f"dry-run Prompt 快照已保存: {prompt_path.resolve()}")
        print("=" * 72)
        for key, value in verification.items():
            if key in {"contract_lengths", "subsection_contract_hashes"}:
                print(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
            else:
                print(f"  {key}: {value}")
        print("=" * 72)
        print(f"  dry-run all_pass: {verification['all_pass']}")
        return 0 if verification["all_pass"] else 1

    client = get_llm_client(model=settings.WRITER_LLM_MODEL)

    def generate_call(messages: list[dict], max_tokens: int) -> str:
        return client.chat_completion(
            messages,
            temperature=0.5,
            max_tokens=max_tokens,
            top_p=0.9,
            max_retries=0,
            prompt_name="scene_reality_contract_v01",
        )

    result = run_v01_experiment(
        baseline_text=baseline_text,
        generate=generate_call,
        inputs=inputs,
        task_id=args.task_id,
        reports_dir=reports_dir,
    )
    b_draft = _render_b_draft(result["candidate_sections"])
    paths = write_outputs(result, reports_dir, b_draft)
    print(json.dumps({
        "model": result["model"],
        "generation_calls": result["generation_calls"],
        "revision_calls": result["revision_calls"],
        "baseline_warning_count": result["baseline_warning_count"],
        "candidate_warning_count": result["candidate_warning_count"],
        "resolved_issue_codes": result["resolved_issue_codes"],
        "new_warning_codes": result["new_warning_codes"],
        "missing_mandatory_events": result["missing_mandatory_events"],
        "automatic_criteria_pass": result["automatic_criteria_pass"],
        "promotion_status": result["promotion_status"],
        "outputs": paths,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
