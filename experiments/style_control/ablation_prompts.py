from __future__ import annotations

from typing import Any

from app.utils.llm_client import estimate_tokens

from .action_style_bridge import (
    compile_action_style_bridge,
    render_action_style_bridge,
)
from .models import StyleDemonstrations, StyleSignature


GLOBAL_PROSE_RULES = """你是一位职业小说作者。只输出小说正文，不要输出标题、解释、分析、提纲或元数据。
必须完成核心场景任务并完整收束；保持人物边界、事实与物件状态一致。
不要机械清点过程动作，不要重复同构句式，不得照抄参考材料或任何示例。"""


BASE_TASK_TEMPLATE = """请完成以下独立小说场景。

## 场景任务
{scene_prompt}

## 人物资料
{characters}

## 世界与连续性事实
{world_facts}

## 必须发生
{mandatory_events}

## 禁止发生
{forbidden_events}

## 风格输入
{style_input}

正文目标长度：{target_chars} 个汉字左右，允许上下浮动 15%。
只输出正文，并确保最后一句完整、核心任务有明确收束。"""


ABLATION_ARMS: dict[str, dict[str, bool]] = {
    "D0": {"signature": True, "positive": False, "negative": False, "reasons": False, "action_bridge": False},
    "D1": {"signature": True, "positive": True, "negative": False, "reasons": False, "action_bridge": False},
    "D2": {"signature": True, "positive": False, "negative": True, "reasons": True, "action_bridge": False},
    "D2R": {"signature": True, "positive": False, "negative": False, "reasons": True, "action_bridge": False},
    "D2A": {"signature": True, "positive": False, "negative": True, "reasons": True, "action_bridge": True},
    "D3": {"signature": True, "positive": True, "negative": True, "reasons": True, "action_bridge": False},
    "F0": {"signature": False, "positive": True, "negative": False, "reasons": False, "action_bridge": False},
}


def render_signature(signature: StyleSignature) -> str:
    labels = {
        "narrative_distance": "叙事距离",
        "viewpoint_permissions": "视角权限",
        "sentence_rhythm": "句式节奏",
        "paragraph_rhythm": "段落节奏",
        "dialogue_function": "对话功能",
        "dialogue_turn_pattern": "对话轮次",
        "emotional_mediation": "情绪中介",
        "diction_register": "措辞语域",
        "imagery_domain": "意象领域",
        "sensory_priority": "感官优先级",
    }
    active = "\n".join(
        f"- {labels[field]}：{getattr(signature, field)}"
        for field in signature.active_dimensions
    )
    prohibitions = "\n".join(f"- {item}" for item in signature.distinctive_prohibitions)
    discriminators = "\n".join(f"- {item}" for item in signature.discriminators)
    return f"""### StyleSignature（只含区分性规则）
{active}

区分性禁忌：
{prohibitions}

与其他目标风格的判别：
{discriminators}"""


def _render_demonstrations(
    demonstrations: StyleDemonstrations,
    *,
    include_positive: bool,
    include_negative: bool,
    include_reasons: bool,
) -> dict[str, str]:
    components: dict[str, str] = {}
    if include_positive:
        lines = []
        for item in demonstrations.positive_demonstrations:
            lines.append(f"[机制：{item.mechanism}]\n{item.text}")
        components["positive_demonstrations"] = "### 安全正向示例\n" + "\n\n".join(lines)
    if include_negative:
        lines = []
        for index, item in enumerate(demonstrations.negative_demonstrations):
            text = f"[错误模式：{item.mechanism}]\n{item.text}"
            if include_reasons and index < len(demonstrations.negative_reasons):
                text += f"\n错误原因：{demonstrations.negative_reasons[index]}"
            lines.append(text)
        components["negative_demonstrations"] = "### 安全反例\n" + "\n\n".join(lines)
    elif include_reasons:
        components["negative_reasons"] = (
            "### 应避免的错误模式\n"
            + "\n".join(f"- {item}" for item in demonstrations.negative_reasons)
        )
    return components


def build_ablation_style_components(
    *,
    arm: str,
    signature: StyleSignature,
    demonstrations: StyleDemonstrations,
    scene_modulation: str,
    scene: dict[str, Any] | None = None,
) -> dict[str, str]:
    if arm not in ABLATION_ARMS:
        raise ValueError(f"unknown ablation arm: {arm}")
    flags = ABLATION_ARMS[arm]
    components: dict[str, str] = {}
    if flags["signature"]:
        components["style_signature"] = render_signature(signature)
        components["scene_modulation"] = f"### SceneModulation\n{scene_modulation}"
    components.update(
        _render_demonstrations(
            demonstrations,
            include_positive=flags["positive"],
            include_negative=flags["negative"],
            include_reasons=flags["reasons"],
        )
    )
    if flags["action_bridge"]:
        if scene is None:
            raise ValueError("action-bridge arm requires the full scene contract")
        bridge = compile_action_style_bridge(signature=signature, scene=scene)
        components["action_style_bridge"] = render_action_style_bridge(bridge)
    return components


def build_ablation_messages(
    *,
    arm: str,
    signature: StyleSignature,
    demonstrations: StyleDemonstrations,
    scene: dict[str, Any],
    shared_context: dict[str, Any],
    target_chars: int,
) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    style_components = build_ablation_style_components(
        arm=arm,
        signature=signature,
        demonstrations=demonstrations,
        scene_modulation=scene["scene_modulation"],
        scene=scene,
    )
    style_input = "\n\n".join(style_components.values())
    task_components = {
        "scene_task": scene["prompt"],
        "characters": "\n".join(f"- {item}" for item in shared_context["characters"]),
        "world_facts": "\n".join(f"- {item}" for item in shared_context["world_facts"]),
        "mandatory_events": "\n".join(f"- {item}" for item in scene["mandatory_events"]),
        "forbidden_events": "\n".join(f"- {item}" for item in scene["forbidden_events"]),
    }
    user_prompt = BASE_TASK_TEMPLATE.format(
        scene_prompt=task_components["scene_task"],
        characters=task_components["characters"],
        world_facts=task_components["world_facts"],
        mandatory_events=task_components["mandatory_events"],
        forbidden_events=task_components["forbidden_events"],
        style_input=style_input,
        target_chars=target_chars,
    )
    raw_components = {
        "global_prose_rules": GLOBAL_PROSE_RULES,
        **task_components,
        **style_components,
        "length_and_output_contract": (
            f"正文目标长度：{target_chars} 个汉字左右，允许上下浮动 15%。"
            "只输出正文，并确保最后一句完整、核心任务有明确收束。"
        ),
    }
    telemetry = {
        key: {
            "characters": len(value),
            "estimated_tokens": estimate_tokens(value),
            "sha256_source": "component_text",
        }
        for key, value in raw_components.items()
    }
    return (
        [
            {"role": "system", "content": GLOBAL_PROSE_RULES},
            {"role": "user", "content": user_prompt},
        ],
        telemetry,
    )
