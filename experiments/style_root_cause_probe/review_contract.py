from __future__ import annotations

import json

from .builder import DEFAULT_OUTPUT, load_json, write_json


def build_review_contract(output_dir=DEFAULT_OUTPUT) -> dict:
    public = load_json(output_dir / "public/blind-review-material.json")
    template = {
        "schema_version": "style-root-cause-blind-review-v0",
        "reviewer_id": "RC-BLIND-REVIEWER-00",
        "scope": {
            "independent_fresh_conversation": True,
            "blind_key_accessed": False,
            "other_reviews_accessed": False,
            "private_material_accessed": False,
            "prompts_or_arm_identity_accessed": False,
            "public_material_only": True,
            "external_or_story_model_called": False,
        },
        "blocks": [
            {
                "public_block_id": block["public_block_id"],
                "assessments": [
                    {
                        "public_text_id": candidate["public_text_id"],
                        "mode_classification": "generic_or_unclear",
                        "hard_task_complete": False,
                        "unauthorized_event_detected": False,
                        "literary_intentionality": 1,
                        "commercial_momentum": 1,
                        "narrative_intentionality": 1,
                        "redundant_explanation": 1,
                        "formulaic_expression": 1,
                        "prompt_structure_leak": 1,
                        "character_motivation_credibility": 1,
                        "overall_ai_taste": 1,
                        "evidence": [{"paragraph_id": "P01", "explanation": "替换为具体证据"}],
                    }
                    for candidate in block["candidates"]
                ],
                "block_note": "",
            }
            for block in public["blocks"]
        ],
        "pairs": [
            {
                "public_pair_id": pair["public_pair_id"],
                "target_mode": pair["pair_type"],
                "candidate_ids": pair["candidate_ids"],
                "target_mode_winners": [pair["candidate_ids"][0]],
                "lower_ai_taste_winners": [pair["candidate_ids"][0]],
                "evidence": [{"paragraph_id": "P01", "explanation": "替换为具体证据"}],
            }
            for pair in public["pairs"]
        ],
        "cross_block_observations": [],
    }
    contract = {
        "schema_version": "style-root-cause-review-contract-v0",
        "instructions": [
            "只读取 public/blind-review-material.json，不读取 private 目录。",
            "先完成单篇判断，再完成配对判断。",
            "问题严重度指标均为1轻5重；正向指标均为1低5高。",
            "所有任务失败和AI味判断必须引用段落证据。",
            "允许并列；并列时 winners 同时列出两个候选。",
        ],
        "template": template,
    }
    write_json(output_dir / "public/blind-review-contract.json", contract)
    return contract


if __name__ == "__main__":
    print(json.dumps(build_review_contract(), ensure_ascii=False, indent=2))

