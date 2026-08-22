"""Run and persist the single-scene expression-only A/B probe."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from .builder import EXPERIMENT_VERSION, build_manifest, build_requests
from .fixture import TARGET_CHARACTERS
from .metrics import evaluate_expression_signals


def _non_negated_positions(text: str, marker: str) -> list[int]:
    positions = []
    start = 0
    while (position := text.find(marker, start)) >= 0:
        prefix = text[max(0, position - 3):position]
        if not any(prefix.endswith(negation) for negation in ("没", "未", "还没", "尚未")):
            positions.append(position)
        start = position + len(marker)
    return positions


def _content_checks(text: str) -> dict[str, bool]:
    no_flash = text.find("别开闪光灯")
    first_shot_positions = [
        position
        for marker in ("按下快门", "摁下快门", "拍下一张", "拍了一张", "快门声响")
        for position in _non_negated_positions(text, marker)
    ]
    first_shot = min(first_shot_positions) if first_shot_positions else -1
    exit_positions = [
        position for position in (text.find("退出"), text.find("退到"), text.find("走出"))
        if position >= 0
    ]
    exit_store = min(exit_positions) if exit_positions else -1
    steps = text.find("台阶")
    work_dissatisfaction_markers = (
        "客户",
        "改稿",
        "修改意见",
        "加班",
        "方案",
        "PPT",
        "工作群",
        "领导",
        "会议",
        "数据报表",
        "辞职",
    )
    saved = any(marker in text for marker in ("保存", "存下", "存了草稿"))
    negative_publish = bool(
        re.search(r"(?:没(?:有|点)?|未|不曾|并未)(?:点击|点)?发布", text)
    )
    text_without_negative_publish = re.sub(
        r"(?:没(?:有|点)?|未|不曾|并未)(?:点击|点)?发布", "", text
    )
    positive_publish = bool(
        re.search(
            r"(?:点击|点了|按下)(?:了)?发布|已经发布|发布到|上传到|把(?:草稿|文章).{0,8}发(?:了|出去|到)",
            text_without_negative_publish,
        )
    )
    return {
        "no_flash_boundary_before_shot": no_flash >= 0 and (first_shot < 0 or no_flash < first_shot),
        "linwan_exits_before_bookstore_steps": (
            exit_store >= 0 and steps >= 0 and exit_store < steps and "夜航船" in text
        ),
        "article_title_present": "一个只肯把时间分给面包的人" in text,
        "zhouye_work_detail_present": "揉面" in text and any(word in text for word in ("掌根", "手掌", "面团")),
        "linwan_work_dissatisfaction_present": any(
            word in text for word in work_dissatisfaction_markers
        ),
        "saved_not_published": saved and (negative_publish or not positive_publish),
    }


def run_probe(generate: Callable[[list[dict]], str]) -> dict:
    requests = build_requests()
    results = []
    for request in requests:
        text = str(generate(request["messages"])).strip()
        results.append(
            {
                "generation_id": request["generation_id"],
                "private_arm": request["private_arm"],
                "public_label": request["public_label"],
                "request_hash": request["request_hash"],
                "text": text,
                "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "expression_metrics": evaluate_expression_signals(text),
                "content_checks": _content_checks(text),
            }
        )
    by_arm = {item["private_arm"]: item for item in results}
    control = by_arm["control"]
    kernel = by_arm["kernel"]
    control_metrics = control["expression_metrics"]
    kernel_metrics = kernel["expression_metrics"]
    kernel_content_complete = all(kernel["content_checks"].values())
    control_content_complete = all(control["content_checks"].values())
    kernel_length_ratio = kernel_metrics["characters"] / TARGET_CHARACTERS
    directional = {
        "targeted_signal_total_nonincrease": (
            kernel_metrics["targeted_signal_total"] <= control_metrics["targeted_signal_total"]
        ),
        "simile_nonincrease": kernel_metrics["simile_count"] <= control_metrics["simile_count"],
        "uplift_closure_nonincrease": (
            kernel_metrics["uplift_closure_count"] <= control_metrics["uplift_closure_count"]
        ),
        "rhythmic_template_nonincrease": (
            kernel_metrics["rhythmic_template_count"] <= control_metrics["rhythmic_template_count"]
        ),
        "kernel_content_complete": kernel_content_complete,
        "kernel_length_within_25pct": 0.75 <= kernel_length_ratio <= 1.25,
    }
    return {
        "version": EXPERIMENT_VERSION,
        "generation_calls": 2,
        "revision_calls": 0,
        "production_effect": False,
        "control_content_complete": control_content_complete,
        "kernel_content_complete": kernel_content_complete,
        "directional_checks": directional,
        "automatic_directional_pass": all(directional.values()),
        "human_blind_review_required": True,
        "promotion_status": "pending_blind_review",
        "results": results,
    }


def render_blind_review(result: dict) -> str:
    lines = [
        "# Anti-AI Expression Kernel v0 — 盲评",
        "",
        "不要判断哪篇使用了规则。只比较表达，不评价情节设计和世界真实性。",
        "",
        "请分别选择：A 更好 / B 更好 / 持平，并写一句证据。",
        "",
        "1. 哪篇更少出现模板化比喻？",
        "2. 哪篇更少重复灯光、声音、冷暖和面粉意象？",
        "3. 哪篇更少在动作后解释人物情绪或意义？",
        "4. 哪篇结尾更像具体场景自然停止，而不是主题升华？",
        "5. 哪篇句法节奏更自然，不像统一模板生成？",
        "6. 总体哪篇更像人写的？",
        "",
    ]
    for item in sorted(result["results"], key=lambda row: row["public_label"]):
        lines.extend([f"## {item['public_label']}", "", item["text"], ""])
    return "\n".join(lines)


def write_outputs(result: dict, reports_dir: Path) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    private_path = reports_dir / "anti-ai-expression-kernel-v0-2026-08-02.json"
    blind_path = reports_dir / "anti-ai-expression-kernel-v0-blind-2026-08-02.md"
    private_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    blind_path.write_text(render_blind_review(result), encoding="utf-8")
    return {"private_result": str(private_path.resolve()), "blind_review": str(blind_path.resolve())}


def render_prompt_snapshot() -> str:
    manifest = build_manifest()
    lines = ["# Anti-AI Expression Kernel v0 — Prompt snapshot", ""]
    for request in manifest["requests"]:
        lines.extend(
            [
                f"## {request['generation_id']} / {request['public_label']}",
                f"request_hash: {request['request_hash']}",
                "",
            ]
        )
        for message in request["messages"]:
            lines.extend([f"### {message['role']}", message["content"], ""])
    return "\n".join(lines)
