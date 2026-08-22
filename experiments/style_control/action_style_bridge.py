from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import StyleSignature


DeltaDomain = Literal[
    "object_state",
    "risk",
    "responsibility_boundary",
    "relationship_signal",
]
DeltaSource = Literal["mandatory_event", "scene_contract"]
ConstraintCategory = Literal[
    "action_selection",
    "dialogue_realization",
    "emotional_externalization",
    "process_compression",
    "prohibition",
]


class ApprovedStateDelta(BaseModel):
    """Read-only content fact supplied by the upstream scene contract."""

    model_config = ConfigDict(extra="forbid")

    delta_id: str = Field(min_length=1)
    domain: DeltaDomain
    description: str = Field(min_length=1)
    source: DeltaSource
    read_only: Literal[True] = True


class ActionStyleConstraint(BaseModel):
    """A realization rule. It has no authority to mutate story content."""

    model_config = ConfigDict(extra="forbid")

    constraint_id: str
    category: ConstraintCategory
    instruction: str
    source_refs: list[str] = Field(min_length=1)
    content_mutation_allowed: Literal[False] = False


class ActionStyleBridgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    scene_type: str
    constraints: list[ActionStyleConstraint] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    content_facts_added: list[str] = Field(default_factory=list, max_length=0)
    relationship_mutations_allowed: Literal[False] = False
    deterministic: Literal[True] = True
    output_hash: str

    @model_validator(mode="after")
    def validate_source_refs(self) -> "ActionStyleBridgeOutput":
        allowed = set(self.source_refs)
        for constraint in self.constraints:
            unknown = set(constraint.source_refs) - allowed
            if unknown:
                raise ValueError(
                    f"{constraint.constraint_id} has unknown source refs: {sorted(unknown)}"
                )
        return self


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compile_action_style_bridge(
    *,
    signature: StyleSignature,
    scene: dict[str, Any],
) -> ActionStyleBridgeOutput:
    """Compile style realization constraints without generating content facts."""

    scene_type = str(scene.get("type", ""))
    if not scene_type.startswith("action"):
        raise ValueError("ActionStyleBridge only accepts action scenes")

    deltas = [
        ApprovedStateDelta.model_validate(item)
        for item in scene.get("approved_state_deltas", [])
    ]
    delta_refs = [f"delta:{item.delta_id}" for item in deltas]
    source_refs = [
        "style:dialogue_function",
        "style:emotional_mediation",
        "style:sentence_rhythm",
        "scene:type",
        "scene:mandatory_events",
        "scene:forbidden_events",
        *delta_refs,
    ]
    constraints = [
        ActionStyleConstraint(
            constraint_id="dialogue-scope",
            category="dialogue_realization",
            instruction="对白只承载选择/风险/优先级；禁流程、核算、关系解释。",
            source_refs=["style:dialogue_function", "scene:mandatory_events"],
        ),
        ActionStyleConstraint(
            constraint_id="action-filter",
            category="action_selection",
            instruction="只展开改变既定状态/风险或完成必发事件的动作；其余过程折叠。",
            source_refs=["style:sentence_rhythm", "scene:mandatory_events"],
        ),
        ActionStyleConstraint(
            constraint_id="process-compression",
            category="process_compression",
            instruction="禁步骤、次数、长数字、清单、物流说明。",
            source_refs=["style:sentence_rhythm", "scene:type"],
        ),
        ActionStyleConstraint(
            constraint_id="emotion-and-authority",
            category="emotional_externalization",
            instruction="情绪借取舍/等待/停顿外化；不新增承诺、关系、责任、归属、事件或结果。",
            source_refs=[
                "style:emotional_mediation",
                "scene:mandatory_events",
                "scene:forbidden_events",
            ],
        ),
    ]
    relationship_refs = [
        f"delta:{item.delta_id}"
        for item in deltas
        if item.domain == "relationship_signal"
    ]
    if relationship_refs:
        constraints.append(
            ActionStyleConstraint(
                constraint_id="relationship-realization-only",
                category="prohibition",
                instruction="已批关系信号只借取舍/等待/停顿呈现；不改方向、主体或内容。",
                source_refs=["style:emotional_mediation", *relationship_refs],
            )
        )

    hash_source = {
        "schema_version": "1.0",
        "style_id": signature.style_id,
        "scene_type": scene_type,
        "approved_state_deltas": [item.model_dump() for item in deltas],
        "constraints": [item.model_dump() for item in constraints],
        "content_facts_added": [],
        "relationship_mutations_allowed": False,
        "deterministic": True,
    }
    return ActionStyleBridgeOutput(
        scene_type=scene_type,
        constraints=constraints,
        source_refs=source_refs,
        output_hash=_hash_payload(hash_source),
    )


def render_action_style_bridge(output: ActionStyleBridgeOutput) -> str:
    lines = "\n".join(f"- {item.instruction}" for item in output.constraints)
    return f"### ActionStyleBridge\n{lines}"
