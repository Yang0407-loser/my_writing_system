"""Scene Reality Contract v0 — one-shot B-draft generation + deterministic eval.

This is a standalone experiment. It never modifies the production Writer, never
reads the A draft as a revision object, makes exactly one generation call per
subsection, and performs zero revision calls.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Callable

from app.config import settings
from app.utils.llm_client import get_llm_client
from app.utils.word_counter import count_chinese_chars
from app.writing.narrative_reality_checks import NarrativeRealityChecker

from .contract import (
    SCENE_REALITY_CONTRACT_V0_TEXT,
    SCENE_REALITY_CONTRACT_VERSION,
    scene_reality_contract_hash,
)
from .inputs import ExperimentInputs, load_experiment_inputs
from .prompting import build_prompt_artifact, build_prompt_values, call_max_tokens_for

EXPERIMENT_VERSION = "scene-reality-contract-v0"

# The seven original problem categories present in the baseline A draft.
ORIGINAL_ISSUE_CODES = [
    "closed_business_activity_without_cause",
    "location_anchor_conflict",
    "knowledge_without_transmission_path",
    "activity_before_established_schedule",
    "recording_without_explicit_permission",
    "institutional_action_marked_complete_without_delivery",
    "process_duration_without_prior_batch",
]

# Contract clause category -> warning codes that auto-verify that clause.
_CONTRACT_WARNING_MAP = {
    "location": {"location_anchor_conflict"},
    "resignation_status": {"institutional_action_marked_complete_without_delivery"},
    "information_provenance": {"knowledge_without_transmission_path"},
    "business_hours": {"closed_business_activity_without_cause", "activity_before_established_schedule"},
    "photography_permission": {"recording_without_explicit_permission"},
    "process_duration": {"process_duration_without_prior_batch"},
}

_SECTION_HEADER_RE = re.compile(
    r"(?m)^(?:第\d+节：.*|第\d+章：.*|[^\n]+)\n(\d+/\d+)\n"
)


def split_baseline_sections(text: str) -> list[str]:
    """Split the A draft into the four subsections using its word-count headers."""
    starts = [m.start() for m in _SECTION_HEADER_RE.finditer(text)]
    if not starts:
        return [text.strip()]
    out: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        out.append(text[start:end].strip())
    return out


def evaluate_text(
    text: str,
    *,
    known_context: str,
    allowed_names: list[str],
) -> list[dict]:
    """Run the production NarrativeRealityChecker over the sections in order."""
    checker = NarrativeRealityChecker(allowed_names=allowed_names)
    records = []
    for index, section_text in enumerate(split_baseline_sections(text), 1):
        record = checker.observe(
            section_text, section=1, subsection=index, known_context=known_context
        )
        if record is not None:
            records.append(record)
    return records


def _warning_codes(records: list[dict]) -> Counter:
    return Counter(
        item["code"] for record in records for item in record.get("warnings") or []
    )


def _prompt_snapshot_header() -> list[str]:
    return [
        "# Scene Reality Contract v0 — 最终发送给 Writer 的 Prompt 快照",
        f"# 版本: {EXPERIMENT_VERSION} / {SCENE_REALITY_CONTRACT_VERSION}",
        f"# 模型: {settings.WRITER_LLM_MODEL}",
        "",
    ]


def _prompt_snapshot_block(prompt: dict) -> list[str]:
    block = [
        "=" * 72,
        f"## 小节 {prompt['subsection']}: {prompt['title']}",
        f"## target_words: {prompt['target_words']}",
        f"## 小节 prompt_hash: {prompt['prompt_hash']}",
        "=" * 72,
    ]
    for message in prompt["messages"]:
        block.append(f"### role: {message['role']}")
        block.append(message["content"])
        block.append("")
    return block


def _append_prompt_to_snapshot(path: Path, prompt: dict, *, first: bool) -> None:
    """Write (or append) one subsection's prompt before its generation call."""
    lines = _prompt_snapshot_header() if first else []
    lines.extend(_prompt_snapshot_block(prompt))
    payload = "\n".join(lines) + "\n"
    if first:
        path.write_text(payload, encoding="utf-8")
    else:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(payload)


def _render_prompt_snapshot(prompts: list[dict]) -> str:
    """Render all four final prompts (system+user) into one auditable snapshot."""
    lines = _prompt_snapshot_header()
    for prompt in prompts:
        lines.extend(_prompt_snapshot_block(prompt))
    return "\n".join(lines)


def _render_b_draft(sections: list[dict]) -> str:
    lines = []
    for section in sections:
        lines.append(f"第{section['subsection']}小节：{section['title']}")
        lines.append(f"{section['char_count']}/{section['target_words']}")
        lines.append("")
        lines.append(section["text"])
        lines.append("")
    return "\n".join(lines)


def compute_target_deviation(sections: list[dict]) -> list[dict]:
    out = []
    for section in sections:
        target = int(section["target_words"])
        actual = int(section["char_count"])
        out.append(
            {
                "subsection": section["subsection"],
                "target_words": target,
                "actual_chars": actual,
                "deviation_chars": actual - target,
                "deviation_ratio": round((actual - target) / target, 4) if target else None,
            }
        )
    return out


def _automatic_contract_compliance(
    candidate_codes: Counter,
) -> dict:
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


def run_experiment(
    *,
    baseline_text: str,
    generate: Callable[[list[dict], int], str],
    inputs: ExperimentInputs,
    contract_text: str = SCENE_REALITY_CONTRACT_V0_TEXT,
    task_id: str = "scene-reality-contract-v0",
    reports_dir: Path | None = None,
) -> dict:
    """Generate the B draft once, evaluate A/B, and return the metrics bundle.

    The prompt snapshot file is written incrementally: each subsection's final
    prompt is appended immediately before its single generation call, so the
    saved snapshot is byte-for-byte what was sent to the model.
    """
    reports_dir = reports_dir or Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    prompt_snapshot_path = (
        reports_dir / "scene-reality-contract-v0-prompt-2026-08-02.txt"
    )

    allowed_names = list(inputs.allowed_names)
    known_context = inputs.known_context

    # --- A baseline evaluation (post-generation comparison only) ---
    baseline_records = evaluate_text(
        baseline_text, known_context=known_context, allowed_names=allowed_names
    )
    baseline_codes = _warning_codes(baseline_records)

    # --- B generation: exactly one call per subsection ---
    prompts: list[dict] = []
    sections: list[dict] = []
    prev_b_texts: list[str] = []
    generation_calls = 0

    sec = inputs.sections[0]
    for sub in sec.get("subsections", []):
        sub_num = int(sub.get("subsection", 0))
        target_words = int(sub.get("target_words", 2000))
        values = build_prompt_values(
            inputs,
            section=1,
            sub_num=sub_num,
            prev_b_texts=prev_b_texts,
            contract_text=contract_text,
        )
        artifact = build_prompt_artifact(
            inputs,
            values,
            section=1,
            sub_num=sub_num,
            task_id=task_id,
            target_words=target_words,
        )
        prompt_hash = artifact.messages_hash
        prompt_entry = {
            "subsection": sub_num,
            "title": sub.get("title", ""),
            "target_words": target_words,
            "prompt_hash": prompt_hash,
            "messages": artifact.messages,
        }
        prompts.append(prompt_entry)
        # Persist this subsection's prompt BEFORE its generation call.
        _append_prompt_to_snapshot(prompt_snapshot_path, prompt_entry, first=sub_num == 1)
        call_max = call_max_tokens_for(target_words)
        # One and only one generation call.
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

    # --- B evaluation ---
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
    contract_hash = scene_reality_contract_hash(contract_text)

    target_deviation = compute_target_deviation(sections)
    contract_compliance = _automatic_contract_compliance(candidate_codes)

    revision_calls = 0
    resolved_count = len(resolved)
    automatic_criteria_pass = (
        resolved_count >= 6
        and not new_codes
        and "unsupported_named_entity" not in candidate_codes
        and revision_calls == 0
        and generation_calls == 4
    )
    promotion_status = "pending_human_review" if automatic_criteria_pass else "failed"

    result = {
        "version": EXPERIMENT_VERSION,
        "model": settings.WRITER_LLM_MODEL,
        "generation_calls": generation_calls,
        "revision_calls": revision_calls,
        "contract_hash": contract_hash,
        "prompt_hash": prompt_hash,
        "baseline_warning_count": int(sum(baseline_codes.values())),
        "candidate_warning_count": int(sum(candidate_codes.values())),
        "baseline_warning_codes": dict(sorted(baseline_codes.items())),
        "candidate_warning_codes": dict(sorted(candidate_codes.items())),
        "resolved_issue_count": resolved_count,
        "resolved_issue_codes": resolved,
        "new_warning_codes": new_codes,
        "contract_compliance": contract_compliance,
        "target_word_deviation": target_deviation,
        "automatic_criteria_pass": automatic_criteria_pass,
        "human_review_required": True,
        "promotion_status": promotion_status,
        "production_effect": False,
        "contract_version": SCENE_REALITY_CONTRACT_VERSION,
        "per_subsection": [
            {
                "subsection": section["subsection"],
                "title": section["title"],
                "target_words": section["target_words"],
                "actual_chars": section["char_count"],
                "prompt_hash": prompt["prompt_hash"],
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
                zip(split_baseline_sections(baseline_text), baseline_records)
            )
        ],
        "candidate_records": candidate_records,
    }
    return result


def write_outputs(result: dict, reports_dir: Path, b_draft: str) -> dict:
    """Persist the four required report files and return their absolute paths.

    The prompt snapshot file was already written incrementally during
    generation (before each call); it is preserved untouched here.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = reports_dir / "scene-reality-contract-v0-prompt-2026-08-02.txt"
    output_path = reports_dir / "scene-reality-contract-v0-output-2026-08-02.txt"
    json_path = reports_dir / "scene-reality-contract-v0-2026-08-02.json"
    md_path = reports_dir / "scene-reality-contract-v0-2026-08-02.md"

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
        "# Scene Reality Contract v0 — 实验结果",
        "",
        f"- 版本: {result['version']}",
        f"- 模型: {result['model']}",
        f"- 生成调用次数: {result['generation_calls']}",
        f"- 修订调用次数: {result['revision_calls']}",
        f"- 合同哈希: {result['contract_hash']}",
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
        "## A 稿各小节 warning",
        "",
    ]
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
    lines.append("## 说明")
    lines.append("")
    lines.append(
        "- 本合同为冻结版本，生成前已保存完整 Prompt 快照，合同位于硬约束区域。"
    )
    lines.append("- B 稿只生成一次，无写后修订，未接入生产 Writer。")
    lines.append("- 最终晋级需人工盲审确认。")
    return "\n".join(lines)
