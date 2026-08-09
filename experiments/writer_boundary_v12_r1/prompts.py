from __future__ import annotations

from typing import Any

from .models import DecisionTicket, R1Protocol, R1Scene
from .runtime import canonical_hash, verify_ticket_consumption


def scene_payload(scene: R1Scene) -> dict[str, Any]:
    return {
        "scene_id": scene.scene_id,
        "scene": scene.scene,
        "characters": scene.characters,
        "world_facts": scene.world_facts,
        "primary_obligation": scene.primary_obligation,
        "decision_shape": scene.decision_shape,
        "long_term_problem": scene.long_term_problem,
        "mandatory_events": scene.mandatory_events,
        "forbidden_events": scene.forbidden_events,
        "style_signature": scene.style_signature.model_dump(),
        "target_chars": scene.target_chars,
    }


def request_config(protocol: R1Protocol, seed: int) -> dict[str, Any]:
    config = protocol.model_config_prose.model_dump()
    return {
        "provider": config["provider"],
        "model": config["model"],
        "temperature": config["temperature"],
        "max_tokens": config["max_tokens"],
        "seed": seed,
        "seed_supported": config["seed_supported"],
        "json_mode": config["json_mode"],
        "thinking": config["thinking"],
    }


def arm_a_request(protocol: R1Protocol, scene: R1Scene, seed: int) -> dict[str, Any]:
    payload = {
        **scene_payload(scene),
        "shared_decision_contract": scene.decision_contract.model_dump(),
    }
    messages = [
        {
            "role": "user",
            "content": {
                "instruction": (
                    "从共享合同的两个合法方案中自行选择且只选择一个，并在同一次"
                    "调用中写成自然中文小说正文。不得输出枚举名、分析、规则或清单。"
                ),
                "input": payload,
            },
        }
    ]
    return {
        "request_config": request_config(protocol, seed),
        "messages": messages,
        "final_messages_hash": canonical_hash(messages),
    }


def arm_b_request(
    protocol: R1Protocol,
    scene: R1Scene,
    seed: int,
    ticket: DecisionTicket,
    ticket_hash: str,
) -> dict[str, Any]:
    verify_ticket_consumption(ticket, ticket_hash, scene)
    payload = {
        **scene_payload(scene),
        "shared_decision_contract": scene.decision_contract.model_dump(),
        "locked_decision": {
            "decision_id": ticket.decision_id,
            "selected_value": ticket.selected_value,
            "selected_definition": ticket.selected_definition,
        },
    }
    messages = [
        {
            "role": "user",
            "content": {
                "instruction": (
                    "选择已经锁定，不得改选。在保留完整合同边界的前提下，写成自然"
                    "中文小说正文。不得输出枚举名、分析、规则或清单。"
                ),
                "input": payload,
            },
        }
    ]
    return {
        "request_config": request_config(protocol, seed),
        "messages": messages,
        "final_messages_hash": canonical_hash(messages),
        "consumed_ticket_hash": ticket_hash,
    }


def arm_c_request(
    protocol: R1Protocol,
    scene: R1Scene,
    seed: int,
    ticket: DecisionTicket,
    ticket_hash: str,
) -> dict[str, Any]:
    verify_ticket_consumption(ticket, ticket_hash, scene)
    payload = {
        **scene_payload(scene),
        "locked_content_boundary": ticket.selected_summary,
    }
    messages = [
        {
            "role": "user",
            "content": {
                "instruction": (
                    "在不改变已锁定内容边界的前提下，自主组织并写成自然中文小说"
                    "正文。不得输出分析、规则或清单，也不必逐句重述边界。"
                ),
                "input": payload,
            },
        }
    ]
    return {
        "request_config": request_config(protocol, seed),
        "messages": messages,
        "final_messages_hash": canonical_hash(messages),
        "consumed_ticket_hash": ticket_hash,
    }

