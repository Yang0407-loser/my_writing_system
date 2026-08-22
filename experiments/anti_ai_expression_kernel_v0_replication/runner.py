"""Run and persist the two-scene replication without revision calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from experiments.anti_ai_expression_kernel_v0.metrics import evaluate_expression_signals

from .builder import EXPERIMENT_VERSION, build_manifest, build_requests
from .fixture import TARGET_CHARACTERS


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _content_checks(scene_id: str, text: str) -> dict[str, bool]:
    if scene_id == "handover":
        folder = min(
            (position for marker in ("文件夹", "交接材料") if (position := text.find(marker)) >= 0),
            default=-1,
        )
        question = text.find("考虑清楚")
        signature = min(
            (position for marker in ("签字", "签下", "签了") if (position := text.find(marker)) >= 0),
            default=-1,
        )
        card = min(
            (position for marker in ("门禁卡", "工牌") if (position := text.find(marker)) >= 0),
            default=-1,
        )
        elevator = text.find("电梯")
        return {
            "folder_handed_over": folder >= 0,
            "decision_question_answered": question >= 0 and _contains_any(
                text[question:], ("考虑清楚了", "想清楚了", "嗯", "是", "清楚")
            ),
            "supervisor_signs": signature >= 0,
            "access_card_left": card >= 0 and _contains_any(
                text[card:], ("放", "留", "搁", "推")
            ),
            "leaves_by_elevator": elevator >= 0 and _contains_any(
                text[elevator:], ("合上", "关上", "下行", "数字", "离开")
            ),
            "event_order_preserved": (
                min(folder, question) >= 0
                and signature > min(folder, question)
                and card >= 0
                and elevator > max(signature, card)
            ),
        }
    if scene_id == "bicycle_chain":
        flip = min(
            (position for marker in ("翻过来", "倒过来", "翻倒", "倒扣") if (position := text.find(marker)) >= 0),
            default=-1,
        )
        help_offer = min(
            (position for marker in ("帮忙", "要帮", "需要帮", "要不要") if (position := text.find(marker)) >= 0),
            default=-1,
        )
        light = min(
            (position for marker in ("照一下", "照着", "照亮", "手电筒", "手机灯") if (position := text.find(marker)) >= 0),
            default=-1,
        )
        repair = min(
            (position for marker in ("套上齿盘", "挂上齿盘", "套回", "装回") if (position := text.find(marker)) >= 0),
            default=-1,
        )
        pedal = min(
            (position for marker in ("转动脚踏", "转了转脚踏", "踩了踩", "转动踏板") if (position := text.find(marker)) >= 0),
            default=-1,
        )
        thanks = min(
            (position for marker in ("谢谢", "谢了", "道谢") if (position := text.find(marker)) >= 0),
            default=-1,
        )
        onward = min(
            (position for marker in ("骑上", "重新上路", "继续赶", "蹬出去") if (position := text.find(marker)) >= 0),
            default=-1,
        )
        return {
            "bike_flipped_for_check": "链条" in text and flip >= 0,
            "passerby_offers_help": help_offer >= 0,
            "asks_for_light_not_repair": light >= 0 and _contains_any(text, ("不用", "不必", "不用修", "照")),
            "chain_reinstalled": repair >= 0 and pedal > repair,
            "thanks_and_rides_on": thanks >= 0 and onward > thanks,
            "countdown_continues": _contains_any(text, ("倒计时", "剩余", "超时", "还剩")),
            "event_order_preserved": flip >= 0 and help_offer > flip and light > help_offer and repair > light and thanks > repair and onward > thanks,
        }
    raise KeyError(scene_id)


def run_replication(generate: Callable[[list[dict]], str]) -> dict:
    requests = build_requests()
    results = []
    for request in requests:
        text = str(generate(request["messages"])).strip()
        results.append(
            {
                "generation_id": request["generation_id"],
                "scene_id": request["scene_id"],
                "scene_title": request["scene_title"],
                "private_arm": request["private_arm"],
                "public_label": request["public_label"],
                "request_hash": request["request_hash"],
                "text": text,
                "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "expression_metrics": evaluate_expression_signals(text),
                "content_checks": _content_checks(request["scene_id"], text),
            }
        )

    scene_results = []
    for scene_id in dict.fromkeys(item["scene_id"] for item in results):
        pair = [item for item in results if item["scene_id"] == scene_id]
        by_arm = {item["private_arm"]: item for item in pair}
        control = by_arm["control"]
        kernel = by_arm["kernel"]
        cm = control["expression_metrics"]
        km = kernel["expression_metrics"]
        checks = {
            "targeted_signal_total_nonincrease": km["targeted_signal_total"] <= cm["targeted_signal_total"],
            "simile_nonincrease": km["simile_count"] <= cm["simile_count"],
            "uplift_closure_nonincrease": km["uplift_closure_count"] <= cm["uplift_closure_count"],
            "rhythmic_template_nonincrease": km["rhythmic_template_count"] <= cm["rhythmic_template_count"],
            "both_content_complete": all(control["content_checks"].values()) and all(kernel["content_checks"].values()),
            "kernel_length_within_25pct": 0.75 <= km["characters"] / TARGET_CHARACTERS <= 1.25,
        }
        scene_results.append(
            {
                "scene_id": scene_id,
                "directional_checks": checks,
                "automatic_directional_pass": all(checks.values()),
            }
        )

    return {
        "version": EXPERIMENT_VERSION,
        "generation_calls": len(requests),
        "revision_calls": 0,
        "production_effect": False,
        "kernel_frozen": True,
        "scene_results": scene_results,
        "automatic_directional_pass": all(item["automatic_directional_pass"] for item in scene_results),
        "human_blind_review_required": True,
        "promotion_status": "pending_blind_review",
        "results": results,
    }


def render_blind_review(result: dict) -> str:
    lines = [
        "# Anti-AI Expression Kernel v0 Replication — 盲评",
        "",
        "逐场景比较匿名文本，只评价表达层面的 AI 味；不要猜测组别。",
        "每题选择 A 更好 / B 更好 / 持平，并提供一处文本证据。",
        "",
        "1. 哪篇更少出现模板化比喻？",
        "2. 哪篇更少重复同类感官意象？",
        "3. 哪篇更少在动作或对白后追加意义解释？",
        "4. 哪篇结尾更自然地停在具体场景？",
        "5. 哪篇句法节奏更自然、较少模板感？",
        "6. 总体哪篇更像人写的？",
        "",
        "每个场景另判断：内容是否足够对等，可以比较表达质量？",
        "",
    ]
    scene_ids = list(dict.fromkeys(item["scene_id"] for item in result["results"]))
    for scene_number, scene_id in enumerate(scene_ids, 1):
        pair = sorted(
            (item for item in result["results"] if item["scene_id"] == scene_id),
            key=lambda item: item["public_label"],
        )
        lines.extend([f"# 场景{scene_number}：{pair[0]['scene_title']}", ""])
        for item in pair:
            short_label = item["public_label"].split("·")[-1]
            lines.extend([f"## {short_label}", "", item["text"], ""])
    return "\n".join(lines)


def render_prompt_snapshot() -> str:
    manifest = build_manifest()
    lines = ["# Anti-AI Expression Kernel v0 Replication — Prompt snapshot", ""]
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


def write_outputs(result: dict, reports_dir: Path) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    private_path = reports_dir / "anti-ai-expression-kernel-v0-replication-2026-08-02.json"
    blind_path = reports_dir / "anti-ai-expression-kernel-v0-replication-blind-2026-08-02.md"
    private_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    blind_path.write_text(render_blind_review(result), encoding="utf-8")
    return {"private_result": str(private_path.resolve()), "blind_review": str(blind_path.resolve())}

