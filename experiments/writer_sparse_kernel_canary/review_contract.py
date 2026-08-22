from __future__ import annotations

import json
from pathlib import Path

from .builder import DEFAULT_OUTPUT, load_json, write_json
from .review import SparseKernelBlindReview


def build_review_contract(output_dir: Path = DEFAULT_OUTPUT) -> dict:
    public = load_json(output_dir / "public/blind-review-material.json")
    expected = [
        {
            "public_block_id": block["public_block_id"],
            "candidate_ids": [
                candidate["public_text_id"] for candidate in block["candidates"]
            ],
        }
        for block in public["blocks"]
    ]
    write_json(
        output_dir / "public/blind-review-schema.json",
        SparseKernelBlindReview.model_json_schema(),
    )
    contract = {
        "schema_version": "writer-sparse-kernel-blind-review-contract-v0",
        "expected_coverage": expected,
        "metrics": {
            "naturalness": "1=生硬，5=自然",
            "less_template": "1=模板痕迹重，5=不模板化",
            "character_credibility": "1=人物工具化，5=人物可信",
            "emotional_residue": "1=收束干净或空泛，5=留下具体未完成感",
            "overall_quality": "1=弱，5=强",
            "mechanicalness": "1=不机械，5=非常机械"
        },
        "winner_rule": (
            "每项 winners 填该 block 中最佳 public_text_id 数组；并列时列出全部"
            "并列者。most_mechanical 填最机械者。"
        ),
        "hard_rule": (
            "hard_task_complete 与 unauthorized_event_detected 必须引用实际段落证据；"
            "不得根据文风推测路线。"
        ),
        "single_total_score_forbidden": True,
        "arm_identity_inference_forbidden": True,
        "model_call_forbidden": True,
    }
    write_json(output_dir / "public/blind-review-contract.json", contract)
    return {"blocks": 4, "candidates": 12}


if __name__ == "__main__":
    print(json.dumps(build_review_contract(), ensure_ascii=False, indent=2))
