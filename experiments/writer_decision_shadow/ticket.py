from __future__ import annotations

import hashlib
import json
from typing import Any

from app.utils.llm_client import estimate_tokens

from .models import (
    DecisionObligation,
    SceneDecisionTicket,
    SourceRef,
    StyleTopologyObligation,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source(
    *,
    ref_id: str,
    source_type: str,
    source_path: str,
    value: Any,
) -> SourceRef:
    return SourceRef(
        ref_id=ref_id,
        source_type=source_type,
        source_path=source_path,
        source_sha256=_sha(_canonical(value)),
    )


def render_compact_ticket() -> str:
    return (
        "硬：M1入库清点；M2漏水加剧；M3先救顾客旧信；M4其余货物仅作临时安排；"
        "H5不得无代价全救；H6禁心理代行动；H7禁擅弃他人物；H8结尾仍为临时方案；"
        "H9无新增关系变化；H10未列人物不得改变结果；H11无替代方案。\n"
        "软：S1展开漏水/救信/临时安排；S2压缩搬运/清点/计数/核算；"
        "S3对白写风险/优先级/责任/决定权；S4情绪借取舍/等待/停顿/物态；"
        "S5禁解释关系/主题；S6流程不主导。"
    )


def compile_ticket(manifest: dict[str, Any]) -> SceneDecisionTicket:
    """Compile only from the upstream manifest; no prose input is accepted."""

    scenes = [item for item in manifest["scenes"] if item["id"] == "SC2"]
    if len(scenes) != 1:
        raise ValueError("expected exactly one SC2 scene")
    scene = scenes[0]
    mandatory = scene["mandatory_events"]
    forbidden = scene["forbidden_events"]
    characters = manifest["shared_context"]["characters"]
    world_facts = manifest["shared_context"]["world_facts"]
    if len(mandatory) < 4 or len(forbidden) < 3:
        raise ValueError("SC2 contract is incomplete")

    refs = [
        _source(
            ref_id="SRC-PROMPT",
            source_type="scene_prompt",
            source_path="scenes[SC2].prompt",
            value=scene["prompt"],
        ),
        *[
            _source(
                ref_id=f"SRC-M{index}",
                source_type="mandatory_event",
                source_path=f"scenes[SC2].mandatory_events[{index - 1}]",
                value=value,
            )
            for index, value in enumerate(mandatory, 1)
        ],
        *[
            _source(
                ref_id=f"SRC-F{index}",
                source_type="forbidden_event",
                source_path=f"scenes[SC2].forbidden_events[{index - 1}]",
                value=value,
            )
            for index, value in enumerate(forbidden, 1)
        ],
        *[
            _source(
                ref_id=f"SRC-C{index}",
                source_type="shared_character",
                source_path=f"shared_context.characters[{index - 1}]",
                value=value,
            )
            for index, value in enumerate(characters, 1)
        ],
        *[
            _source(
                ref_id=f"SRC-W{index}",
                source_type="shared_world_fact",
                source_path=f"shared_context.world_facts[{index - 1}]",
                value=value,
            )
            for index, value in enumerate(world_facts, 1)
        ],
        _source(
            ref_id="SRC-MOD",
            source_type="scene_modulation",
            source_path="scenes[SC2].scene_modulation",
            value=scene["scene_modulation"],
        ),
    ]

    hard = [
        DecisionObligation(
            decision_id=f"M{index}",
            category="mandatory_event",
            claim=value,
            allowed_values=["present"],
            expected_state="present",
            source_refs=[f"SRC-M{index}"],
            verification_mode="presence",
        )
        for index, value in enumerate(mandatory[:4], 1)
    ]
    hard.extend(
        [
            DecisionObligation(
                decision_id="H5",
                category="state_delta",
                claim="全部货物不能毫无代价地获救。",
                allowed_values=["respected", "violated", "unverifiable"],
                expected_state="respected",
                source_refs=["SRC-F1"],
                verification_mode="absence",
            ),
            DecisionObligation(
                decision_id="H6",
                category="fact_authority",
                claim="长篇心理独白不能替代抢救行动。",
                allowed_values=["respected", "violated", "unverifiable"],
                expected_state="respected",
                source_refs=["SRC-F2"],
                verification_mode="absence",
            ),
            DecisionObligation(
                decision_id="H7",
                category="fact_authority",
                claim="任何一方不得未经讨论擅自丢弃他人物品。",
                allowed_values=["respected", "violated", "unverifiable"],
                expected_state="respected",
                source_refs=["SRC-F3"],
                verification_mode="authority_check",
            ),
            DecisionObligation(
                decision_id="H8",
                category="closure_state",
                claim="结尾仍只形成其余货物的临时方案。",
                allowed_values=["present", "absent", "contradicted", "unverifiable"],
                expected_state="temporary_plan",
                source_refs=["SRC-M4"],
                verification_mode="state_match",
            ),
            DecisionObligation(
                decision_id="H9",
                category="relationship_authority",
                claim="不得产生上游未批准的人物关系变化。",
                allowed_values=["respected", "violated", "unverifiable"],
                expected_state="none",
                source_refs=["SRC-C1", "SRC-C2", "SRC-W4"],
                verification_mode="authority_check",
            ),
            DecisionObligation(
                decision_id="H10",
                category="character_authority",
                claim="未列入场景人物的角色不得推动、解决或改变场景结果。",
                allowed_values=["respected", "violated", "unverifiable"],
                expected_state="listed_characters_only",
                source_refs=["SRC-PROMPT", "SRC-C1", "SRC-C2"],
                verification_mode="authority_check",
            ),
            DecisionObligation(
                decision_id="H11",
                category="fact_authority",
                claim="不得引入替代原临时安排的新解决方案。",
                allowed_values=["respected", "violated", "unverifiable"],
                expected_state="no_replacement_solution",
                source_refs=["SRC-M4", "SRC-F1"],
                verification_mode="authority_check",
            ),
        ]
    )

    soft = [
        StyleTopologyObligation(
            decision_id="S1",
            category="action_expansion",
            claim="漏水风险、旧信抢救和临时方案得到足够展开。",
            source_refs=["SRC-M2", "SRC-M3", "SRC-M4"],
        ),
        StyleTopologyObligation(
            decision_id="S2",
            category="process_compression",
            claim="重复搬运、逐项清点、连续计数和成本核算得到压缩。",
            source_refs=["SRC-MOD", "SRC-M1"],
        ),
        StyleTopologyObligation(
            decision_id="S3",
            category="dialogue_function",
            claim="对白主要承担风险确认、行动优先级、责任边界和决定权保留。",
            source_refs=["SRC-MOD", "SRC-W4"],
        ),
        StyleTopologyObligation(
            decision_id="S4",
            category="emotion_channel",
            claim="情绪主要通过取舍、等待确认、停顿、物件状态或未完成动作呈现。",
            source_refs=["SRC-PROMPT"],
        ),
        StyleTopologyObligation(
            decision_id="S5",
            category="direct_explanation",
            claim="叙述不直接解释人物关系、信任、责任本质或主题。",
            source_refs=["SRC-PROMPT", "SRC-W4"],
        ),
        StyleTopologyObligation(
            decision_id="S6",
            category="process_compression",
            claim="操作流程不成为场景的主要组织结构。",
            source_refs=["SRC-MOD"],
        ),
    ]

    compact = render_compact_ticket()
    compact_tokens = estimate_tokens(compact)
    source_contract_hash = _sha(_canonical(manifest))
    hash_source = {
        "schema_version": "1.0",
        "ticket_id": "writer-decision-shadow-v0-sc2",
        "scene_id": "SC2",
        "source_contract_hash": source_contract_hash,
        "source_refs": [item.model_dump() for item in refs],
        "hard_obligations": [item.model_dump() for item in hard],
        "soft_topology_obligations": [item.model_dump() for item in soft],
        "relationship_delta": "none",
        "new_content_facts": [],
        "content_authority_owner": "upstream_scene_contract",
        "ticket_token_estimate": compact_tokens,
        "compact_rendering": compact,
        "compact_rendering_hash": _sha(compact),
        "deterministic": True,
    }
    return SceneDecisionTicket(
        **hash_source,
        ticket_hash=_sha(_canonical(hash_source)),
    )
