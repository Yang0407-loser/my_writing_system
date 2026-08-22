from __future__ import annotations

from typing import Any

ORIGINAL = ["process_log", "direct_explanation", "abstract_emotion", "event_overengineering", "logistics_dialogue"]
STRUCTURAL = ["obligation_sequence_visibility", "dialogue_slot_visibility", "local_choice_starvation", "over_complete_structure", "constraint_reconfirmation"]
PAIR_FIELDS = ["naturalness", "less_template", "character_credibility", "emotional_residue", "overall_quality", "more_mechanical"]


def template(public: dict[str, Any], reviewer_id: str = "") -> dict[str, Any]:
    blank = lambda k: {"category": k, "detected": None, "paragraphs": [], "description": ""}
    return {
        "reviewer_id": reviewer_id,
        "scope": {
            "independent_new_task": True, "blind_key_accessed": False,
            "other_reviews_accessed": False,
            "prompts_tickets_summaries_results_accessed": False,
            "public_material_only": True,
        },
        "samples": [{
            "text_id": row["text_id"],
            "hard_checks": {k: None for k in ("mandatory_events_complete", "new_character", "new_solution", "relationship_change", "temporary_ending", "boundary_fidelity")},
            "original_witnesses": [blank(k) for k in ORIGINAL],
            "structural_diagnostics": [blank(k) for k in STRUCTURAL],
        } for row in public["texts"]],
        "pairs": [{"pair_id": row["pair_id"], **{k: None for k in PAIR_FIELDS}, "confidence": None} for row in public["pairs"]],
    }


def instructions() -> str:
    return """# Boundary Canary 独立盲审

只读 public、统一说明和自己的模板。不得读取 key、prompts、tickets、summaries、
results、manifest、旧实验或其他评审。逐篇完成六项硬检查、五类原 witness 和五类
结构诊断；detected=true 必须有段落 ID 与具体说明，false 必须保持空段落数组。

结构诊断：obligation_sequence_visibility 检测至少三个相邻单元逐项完成不同场景要求；
dialogue_slot_visibility 检测至少三个可互换的功能对白簇；local_choice_starvation 必须
用具体段落证明四类以上组织方式高度可预测；over_complete_structure 检测每项义务均被
显式关闭并削弱余味；constraint_reconfirmation 检测同一边界无新信息地重复至少三次。
这些只用于诊断，不得合成总分。

配对选项只能为 text_1/text_2/tie；more_mechanical 为负向指标；confidence 必须是
1–5 严格整数。不要猜实验路线、做 S1/S2/S3 分类或给单一总分。
"""

