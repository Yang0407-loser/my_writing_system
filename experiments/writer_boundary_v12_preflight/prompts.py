from __future__ import annotations

import json
from typing import Any

from .contract import contract_hash, contract_payload
from .models import SharedDecisionContract


def _render(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def shared_scene_payload(scene: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_id": scene["scene_id"],
        "scene": scene["scene"],
        "characters": scene["characters"],
        "world_facts": scene["world_facts"],
        "mandatory_events": scene["mandatory_events"],
        "forbidden_events": scene["forbidden_events"],
    }


def w0_prompt_snapshot(scene: dict[str, Any], contract: SharedDecisionContract) -> dict[str, Any]:
    payload = {
        **shared_scene_payload(scene),
        "shared_decision_contract": contract_payload(contract),
        "decision_contract_hash": contract_hash(contract),
        "style_signature": scene["style_signature"],
        "target_chars": scene["prose"]["target_chars"],
        "output": "只输出中文小说正文，不加标题、说明或分析。",
    }
    text = (
        "从 shared_decision_contract 的两个合法方案中自行选择且只选择一个，"
        "并在同一次调用中自主组织成自然小说正文。不要在正文中输出枚举名。\n"
        + _render(payload)
    )
    return {"route": "W0", "messages": [{"role": "user", "content": text}], "payload": payload}


def boundary_maker_prompt_snapshot(
    scene: dict[str, Any], contract: SharedDecisionContract, repeat: int
) -> dict[str, Any]:
    values = [item.value for item in contract.allowed_values]
    legal = " 或 ".join(
        _render({"selected_temporary_solution": value}) for value in values
    )
    payload = {
        **shared_scene_payload(scene),
        "repeat": repeat,
        "shared_decision_contract": contract_payload(contract),
        "decision_contract_hash": contract_hash(contract),
    }
    text = (
        "只从 shared_decision_contract 中选择一个临时方案。输出必须且只能是一个 "
        'JSON 对象，唯一键为 "selected_temporary_solution"，值必须是字符串。'
        "禁止输出数组、allowed_values、输入 schema、理由、分析或其他键。"
        f"合法输出只能是：{legal}\n" + _render(payload)
    )
    return {"route": "BOUNDARY_MAKER", "messages": [{"role": "user", "content": text}], "payload": payload}


def deterministic_summary(selected: str) -> str:
    summaries = {
        "raised_mesh_rack": "今晚的取舍已经确定：唯一的防水箱留给顾客暂存的手写日记，书店校样本只移到高处通风网架临时避水。长期干燥、修复和窗体处理留到天亮。",
        "single_absorbent_wrap": "今晚的取舍已经确定：唯一的防水箱留给顾客暂存的手写日记，书店校样本只用一层吸水材料临时包覆。长期干燥、修复和窗体处理留到天亮。",
    }
    return summaries[selected]


def w2_realizer_prompt_snapshot(scene: dict[str, Any], selected: str) -> dict[str, Any]:
    payload = {
        "scene_id": scene["scene_id"],
        "scene": scene["scene"],
        "characters": scene["characters"],
        "world_facts": scene["world_facts"],
        "style_signature": scene["style_signature"],
        "content_boundary": deterministic_summary(selected),
        "target_chars": scene["prose"]["target_chars"],
        "output": "只输出中文小说正文，不加标题、说明或分析。",
    }
    text = (
        "在不改变已确定内容边界的前提下，自主组织并写成自然小说正文；"
        "不必逐句重述边界。\n" + _render(payload)
    )
    return {"route": "W2_REALIZER", "messages": [{"role": "user", "content": text}], "payload": payload}

