"""Build the frozen two-arm, one-scene expression A/B requests."""

from __future__ import annotations

import hashlib
import json
import random

from .fixture import FIXTURE_VERSION, SCENE_CONTENT, TARGET_CHARACTERS
from .kernel import KERNEL_VERSION, expression_kernel_hash, render_expression_kernel


EXPERIMENT_VERSION = "anti-ai-expression-kernel-v0"
RANDOMIZATION_SEED = 31871

SYSTEM_PROMPT = """你是一名中文小说作者。根据固定内容合同写一个完整小节。
只输出正文，不输出标题、提纲、分析、规则或字段名。
使用第三人称近距离叙述，视点跟随林晚。正文约800个中文字符。
必须完成全部事件，保持人物关系、事实、事件顺序和结束状态。"""


def _payload(*, treatment: bool) -> dict:
    payload = {
        "fixture_version": FIXTURE_VERSION,
        "target_characters": TARGET_CHARACTERS,
        "fixed_content_contract": SCENE_CONTENT,
    }
    if treatment:
        payload["expression_kernel"] = render_expression_kernel()
    return payload


def build_requests() -> list[dict]:
    arms = ["control", "kernel"]
    random.Random(RANDOMIZATION_SEED).shuffle(arms)
    requests = []
    for ordinal, arm in enumerate(arms, 1):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(_payload(treatment=arm == "kernel"), ensure_ascii=False, indent=2),
            },
        ]
        digest = hashlib.sha256(
            json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        requests.append(
            {
                "version": EXPERIMENT_VERSION,
                "generation_id": f"AAE-{ordinal:02d}",
                "ordinal": ordinal,
                "private_arm": arm,
                "public_label": f"文本{chr(64 + ordinal)}",
                "messages": messages,
                "request_hash": digest,
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
        "generation_requests": len(requests),
        "single_scene": True,
        "repeats": 1,
        "production_effect": False,
        "commercial_policy_present": False,
        "reality_policy_present": False,
        "requests": requests,
    }
