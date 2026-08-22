from __future__ import annotations

import json
from typing import Any

from .models import DecisionTicket


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def shared_prose_contract(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_id": fixture["scene_id"],
        "scene": fixture["scene"],
        "characters": fixture["characters"],
        "world_facts": fixture["world_facts"],
        "style_signature": fixture["style_signature"],
        "target_chars": fixture["prose"]["target_chars"],
        "output": "只输出中文小说正文，不加标题、说明或分析。",
    }


def decision_prompt(fixture: dict[str, Any], repeat: int) -> str:
    payload = {
        "scene": fixture["scene"],
        "characters": fixture["characters"],
        "world_facts": fixture["world_facts"],
        "mandatory_events": fixture["mandatory_events"],
        "forbidden_events": fixture["forbidden_events"],
        "decision_space": fixture["decision_space"],
        "repeat": repeat,
    }
    return (
        "你是 Decision Maker。只能从封闭选项中选择。输出严格 JSON，"
        "不得输出原因、分析、解释或思维过程；不得新增事实。\n" + _j(payload)
    )


def w0_prompt(fixture: dict[str, Any]) -> str:
    payload = {
        **shared_prose_contract(fixture),
        "mandatory_events": fixture["mandatory_events"],
        "forbidden_events": fixture["forbidden_events"],
    }
    return "一次完成场景理解、叙事选择与小说实现。\n" + _j(payload)


def compact_ticket(ticket: DecisionTicket) -> dict[str, Any]:
    return {
        "scene_id": ticket.scene_id,
        "repeat": ticket.repeat,
        "selected_decisions": ticket.selected_decisions.model_dump(),
        "locked": ticket.locked,
        "ticket_hash": ticket.ticket_hash,
    }


def w1_prompt(fixture: dict[str, Any], ticket: DecisionTicket) -> str:
    payload = {
        **shared_prose_contract(fixture),
        "locked_decisions": compact_ticket(ticket),
    }
    return (
        "在不改变已锁定选择的前提下，将它们实现为自然小说正文。"
        "不要解释规则，不要补造人物、关系变化或长期解决方案。\n" + _j(payload)
    )

