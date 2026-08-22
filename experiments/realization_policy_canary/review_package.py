from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .experiment import DEFAULT_OUTPUT, load_json, write_json
from .review import RealizationPolicyBlindReview


def build_review_package(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    public = load_json(output_dir / "public/blind-review-material.json")
    expected = [
        {
            "public_block_id": block["public_block_id"],
            "candidate_ids": [
                item["public_text_id"] for item in block["candidates"]
            ],
        }
        for block in public["blocks"]
    ]
    contract = {
        "schema_version": "realization-policy-review-contract-v1",
        "expected_coverage": expected,
        "metrics": {
            "naturalness": "1=生硬，5=自然",
            "less_template": "1=模板痕迹重，5=不模板化",
            "character_credibility": "1=人物工具化，5=人物可信",
            "emotional_residue": "1=空泛或过度收束，5=留下具体未完成感",
            "overall_quality": "1=弱，5=强",
            "mechanicalness": "1=不机械，5=非常机械"
        },
        "hard_rule": "对照每个block公开的内容边界判断，必须引用实际段落证据。",
        "winner_rule": "每项填最佳匿名ID；并列时同时填两项。most_mechanical填最机械者。",
        "single_total_score_forbidden": True,
        "arm_identity_inference_forbidden": True,
        "blind_key_access_forbidden": True,
        "other_review_access_forbidden": True,
    }
    write_json(
        output_dir / "public/blind-review-schema.json",
        RealizationPolicyBlindReview.model_json_schema(),
    )
    write_json(output_dir / "public/blind-review-contract.json", contract)
    template = {
        "schema_version": "realization-policy-blind-review-v1",
        "reviewer_id": "RP-BLIND-REVIEWER-00",
        "scope": {
            "independent_fresh_conversation": True,
            "blind_key_accessed": False,
            "other_reviews_accessed": False,
            "private_material_accessed": False,
            "prompts_or_arm_identity_accessed": False,
            "public_material_only": True,
            "external_or_story_model_called": False
        },
        "blocks": [],
        "cross_block_observations": []
    }
    write_json(output_dir / "public/blind-review-template.json", template)
    return {"blocks": len(expected), "candidates": 16}


if __name__ == "__main__":
    print(json.dumps(build_review_package(), ensure_ascii=False, indent=2))
