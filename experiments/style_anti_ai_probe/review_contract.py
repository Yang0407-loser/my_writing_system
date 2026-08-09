from __future__ import annotations

import json
from .builder import DEFAULT_OUTPUT, load_json, write_json


def build_review_contract(output_dir=DEFAULT_OUTPUT) -> dict:
    public = load_json(output_dir / "public/blind-review-material.json")
    def evidence(public_id: str) -> dict:
        return {"public_text_id": public_id, "paragraph_id": "P01", "explanation": "替换为具体证据"}
    def assessment(public_id: str) -> dict:
        return {
            "public_text_id": public_id, "hard_task_complete": False, "unauthorized_event_detected": False,
            "commercial_momentum": 1, "character_motivation_credibility": 1, "specificity": 1, "naturalness": 1,
            "redundant_explanation": 1, "formulaic_expression": 1, "summary_closure": 1,
            "prompt_structure_leak": 1, "overall_ai_taste": 1, "evidence": [evidence(public_id)],
        }
    template = {
        "schema_version": "style-anti-ai-blind-review-v0", "reviewer_id": "AA-BLIND-REVIEWER-00",
        "scope": {"independent_fresh_conversation": True, "blind_key_accessed": False, "other_reviews_accessed": False, "private_material_accessed": False, "prompts_or_arm_identity_accessed": False, "public_material_only": True, "external_or_story_model_called": False},
        "blocks": [], "cross_block_observations": [],
    }
    for block in public["blocks"]:
        ids = [item["public_text_id"] for item in block["candidates"]]
        template["blocks"].append({
            "public_block_id": block["public_block_id"], "assessments": [assessment(i) for i in ids],
            "better_commercial_execution": [ids[0]], "lower_ai_taste": [ids[0]], "better_overall": [ids[0]],
            "pair_evidence": [evidence(ids[0])], "block_note": "",
        })
    contract = {
        "schema_version": "style-anti-ai-review-contract-v0",
        "instructions": [
            "只读取公开盲评材料，不读取private目录或其他评审。",
            "先逐篇评分，再做同block配对；允许并列。",
            "正向指标1低5高；问题严重度及overall_ai_taste为1轻5重。",
            "任务失败、AI味和配对判断必须引用具体候选与段落证据。",
        ],
        "template": template,
    }
    write_json(output_dir / "public/blind-review-contract.json", contract)
    return contract


if __name__ == "__main__":
    print(json.dumps(build_review_contract(), ensure_ascii=False, indent=2))

