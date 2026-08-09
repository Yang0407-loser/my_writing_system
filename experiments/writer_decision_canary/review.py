from __future__ import annotations

from typing import Any


WITNESSES = [
    "process_log",
    "direct_explanation",
    "abstract_emotion",
    "event_overengineering",
    "logistics_dialogue",
]
PAIR_FIELDS = [
    "naturalness",
    "less_template",
    "character_credibility",
    "emotional_residue",
    "overall_quality",
    "more_mechanical",
]


def build_review_template(public: dict[str, Any], reviewer_id: str = "") -> dict[str, Any]:
    samples = []
    for item in public["texts"]:
        samples.append({
            "text_id": item["text_id"],
            "hard_checks": {
                "mandatory_events_complete": None,
                "new_character": None,
                "new_solution": None,
                "relationship_change": None,
                "temporary_ending": None,
                "decision_fidelity": None,
            },
            "witnesses": [
                {"category": kind, "detected": None, "paragraphs": [], "description": ""}
                for kind in WITNESSES
            ],
        })
    pairs = []
    for pair in public["pairs"]:
        row = {"pair_id": pair["pair_id"], **{field: None for field in PAIR_FIELDS}}
        row["confidence"] = None
        pairs.append(row)
    return {
        "reviewer_id": reviewer_id,
        "scope": {
            "independent_new_task": True,
            "blind_key_accessed": False,
            "other_reviews_accessed": False,
            "prompts_tickets_results_accessed": False,
            "public_material_only": True,
        },
        "samples": samples,
        "pairs": pairs,
    }


def reviewer_instructions() -> str:
    return """# Mini-Canary blind review

只读取公开材料和自己的模板。不得读取 blind key、其他评审、prompts、tickets、
results 或 manifest。必须在一个全新的独立任务中完成。

五类局部 witness：
1. process_log：连续至少两段主要是搬运、设置、清点、分类、计数、成本或物流，
   且没有改变风险、选择、责任边界、关键物件状态或结局；记录段落 ID。
2. direct_explanation：叙述者替读者解释对白、边界、信任、退让、责任、关系或主题。
3. abstract_emotion：直接命名情绪并替代动作呈现；说明为何它替代了叙事动作。
4. event_overengineering：新增人物、事故或危机，并显著改变风险、方案或高潮。
5. logistics_dialogue：连续对白主要播报步骤、参数、成本、数量或安排，且不改变决策。

detected=true 时必须填写段落 ID 和具体说明。配对选项只能是 text_1、text_2、tie；
confidence 必须是 1–5 的整数。不要猜测实验路线，不要输出综合总分。
"""

