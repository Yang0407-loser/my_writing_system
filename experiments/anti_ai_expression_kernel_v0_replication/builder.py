"""Build four requests while keeping the expression kernel byte-for-byte frozen."""

from __future__ import annotations

import hashlib
import json
import random

from experiments.anti_ai_expression_kernel_v0.kernel import (
    KERNEL_VERSION,
    expression_kernel_hash,
    render_expression_kernel,
)

from .fixture import FIXTURE_VERSION, SCENES, TARGET_CHARACTERS


EXPERIMENT_VERSION = "anti-ai-expression-kernel-v0-replication"
RANDOMIZATION_SEED = 59107


def _system_prompt(scene: dict) -> str:
    return (
        "你是一名中文小说作者。根据固定内容合同写一个完整小节。\n"
        "只输出正文，不输出标题、提纲、分析、规则或字段名。\n"
        f"使用{scene['viewpoint']}。正文约{TARGET_CHARACTERS}个中文字符。\n"
        "必须完成全部事件，保持人物关系、事实、事件顺序和结束状态。"
    )


def _payload(scene: dict, *, treatment: bool) -> dict:
    payload = {
        "fixture_version": FIXTURE_VERSION,
        "target_characters": TARGET_CHARACTERS,
        "fixed_content_contract": scene,
    }
    if treatment:
        payload["expression_kernel"] = render_expression_kernel()
    return payload


def build_requests() -> list[dict]:
    requests = []
    rng = random.Random(RANDOMIZATION_SEED)
    for scene_number, scene in enumerate(SCENES, 1):
        arms = ["control", "kernel"]
        rng.shuffle(arms)
        for label_number, arm in enumerate(arms, 1):
            messages = [
                {"role": "system", "content": _system_prompt(scene)},
                {
                    "role": "user",
                    "content": json.dumps(
                        _payload(scene, treatment=arm == "kernel"),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
            ]
            request_hash = hashlib.sha256(
                json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            requests.append(
                {
                    "version": EXPERIMENT_VERSION,
                    "generation_id": f"AAER-{scene_number:02d}-{label_number:02d}",
                    "scene_id": scene["scene_id"],
                    "scene_title": scene["title"],
                    "private_arm": arm,
                    "public_label": f"场景{scene_number}·文本{chr(64 + label_number)}",
                    "messages": messages,
                    "request_hash": request_hash,
                }
            )
    return requests


def build_manifest() -> dict:
    requests = build_requests()
    return {
        "version": EXPERIMENT_VERSION,
        "fixture_version": FIXTURE_VERSION,
        "kernel_version": KERNEL_VERSION,
        "kernel_hash": expression_kernel_hash(),
        "scene_count": len(SCENES),
        "generation_requests": len(requests),
        "repeats_per_arm_per_scene": 1,
        "production_effect": False,
        "commercial_policy_present": False,
        "reality_policy_present": False,
        "requests": requests,
    }

