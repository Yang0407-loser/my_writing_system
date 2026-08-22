from __future__ import annotations

from typing import Any

from .models import AssignmentTicket, RequestEnvelope, R2Protocol
from .runtime import envelope_hash, verify_assignment


BC_INSTRUCTION = (
    "按照输入中已经确定的内容边界，写成自然中文小说正文。"
    "不得改变该边界，不得输出分析、规则、枚举名或检查清单。"
)


def scene_payload(scene) -> dict[str, Any]:
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


def build_envelope(
    *,
    protocol: R2Protocol,
    protocol_hash: str,
    block: dict[str, Any],
    arm: str,
    text_id: str,
    scene,
    ticket: AssignmentTicket | None,
    assignment_hash: str | None,
    matrix_hash: str,
) -> tuple[RequestEnvelope, str]:
    base = scene_payload(scene)
    if arm == "A":
        instruction = (
            "从共享合同的两个合法方案中自行选择且只选择一个，并写成自然中文小说正文。"
            "不得输出分析、规则、枚举名或检查清单。"
        )
        payload = {**base, "shared_decision_contract": scene.decision_contract.model_dump()}
        if ticket is not None or assignment_hash is not None:
            raise ValueError("arm A must not consume assignment")
    else:
        if ticket is None or assignment_hash is None:
            raise ValueError("arms B/C require assignment")
        verify_assignment(
            ticket,
            expected_block_id=block["block_id"],
            expected_scene_id=scene.scene_id,
            expected_matrix_hash=matrix_hash,
            scene=scene,
        )
        instruction = BC_INSTRUCTION
        if arm == "B":
            payload = {
                **base,
                "shared_decision_contract": scene.decision_contract.model_dump(),
                "locked_assignment": {
                    "decision_id": ticket.decision_id,
                    "selected_value": ticket.selected_value,
                    "selected_definition": ticket.selected_definition,
                },
            }
        elif arm == "C":
            payload = {**base, "locked_content_boundary": ticket.selected_summary}
        else:
            raise ValueError("unknown arm")
    messages = [{"role": "user", "content": {"instruction": instruction, "input": payload}}]
    envelope = RequestEnvelope(
        experiment_id=protocol.experiment_id,
        block_id=block["block_id"],
        text_id=text_id,
        arm=arm,
        request_nonce=block["request_nonces"][arm],
        provider_config=protocol.provider_config,
        messages=messages,
        protocol_sha256=protocol_hash,
        assignment_sha256=assignment_hash,
    )
    return envelope, envelope_hash(envelope)

