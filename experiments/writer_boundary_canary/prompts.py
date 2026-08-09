from __future__ import annotations

import json
from typing import Any

from .models import CompiledSummary


def _render(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def shared_contract(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_id": "SC4", "scene": fixture["scene"],
        "characters": fixture["characters"], "world_facts": fixture["world_facts"],
        "style_signature": fixture["style_signature"],
        "target_chars": fixture["prose"]["target_chars"],
        "output": "只输出中文小说正文，不加标题、说明或分析。",
    }


def boundary_prompt(fixture: dict[str, Any], repeat: int) -> str:
    allowed = fixture["boundary_space"]["store_item_temporary_handling"]
    return (
        "你只决定书店校样本采用哪一种白名单临时处置。\n"
        "从 allowed_values 中选择且只选择一个值。\n"
        "输出必须且只能是一个 JSON 对象，并且只能包含键 "
        '"store_item_temporary_handling"。\n'
        "该键的值必须是一个字符串，不能是数组。\n"
        "禁止输出 allowed_values、allowed_handling 或任何其他键。\n"
        "不要输出理由、分析、正文计划或思维过程。\n"
        "合法输出只能是以下两个对象之一："
        '{"store_item_temporary_handling":"raised_mesh_rack"} 或 '
        '{"store_item_temporary_handling":"single_absorbent_wrap"}。\n'
        + _render({
            "repeat": repeat,
            "scene": fixture["scene"],
            "characters": fixture["characters"],
            "world_facts": fixture["world_facts"],
            "mandatory_events": fixture["mandatory_events"],
            "forbidden_events": fixture["forbidden_events"],
            "selection_contract": {
                "required_output_key": "store_item_temporary_handling",
                "allowed_values": allowed,
                "choose_exactly": 1,
            },
        })
    )


def w0_prompt(fixture: dict[str, Any]) -> str:
    return "一次完成场景理解、局部叙事选择与小说实现。\n" + _render({
        **shared_contract(fixture),
        "mandatory_events": fixture["mandatory_events"],
        "forbidden_events": fixture["forbidden_events"],
    })


def w2_prompt(fixture: dict[str, Any], summary: CompiledSummary) -> str:
    return "在不改变已确定内容边界的前提下，自主组织并写成自然小说正文；不必逐句重述边界。\n" + _render({
        **shared_contract(fixture),
        "content_boundary": summary.compiled_summary,
    })
