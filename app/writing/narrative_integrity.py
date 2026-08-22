"""Narrative credibility constraints shared by every genre and prose style."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Iterable, Mapping


NARRATIVE_INTEGRITY_VERSION = "narrative-integrity-v0"
WORLD_PRESSURE_VERSION = "world-pressure-contract-v0"


@dataclass(frozen=True)
class NarrativeIntegrityPolicy:
    version: str
    rules: tuple[str, ...]
    required_event_count: int
    source_refs: tuple[dict[str, str], ...]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WorldPressureContract:
    version: str
    preset: str
    institutional_rules: tuple[str, ...]
    business_rules: tuple[str, ...]
    space_time_rules: tuple[str, ...]
    social_boundary_rules: tuple[str, ...]
    material_consequence_rules: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def compile_narrative_integrity(
    *, required_events: Iterable[Mapping[str, object]] = (),
) -> NarrativeIntegrityPolicy:
    refs: list[dict[str, str]] = []
    for item in required_events or ():
        source_id = str(item.get("source_id", "")).strip()
        text_hash = str(item.get("text_hash", "")).strip()
        if source_id or text_hash:
            refs.append({"source_id": source_id, "text_hash": text_hash})
    return NarrativeIntegrityPolicy(
        version=NARRATIVE_INTEGRITY_VERSION,
        rules=(
            "同一信息、意象或动作再次出现时，必须改变含义、关系或局面；否则删除，不做近义复述。",
            "场景已经让读者看出的情绪和主题，不再由旁白、内心独白或段尾金句解释。",
            "不强迫段落获得文学化闭合；可以停在未完成动作、现实干扰、误解或迫近后果上。",
            "关键事件必须由人物欲望、阻力和前序后果触发，不按大纲或任务清单顺序机械升级。",
            "主要角色各有独立目标和边界；配角可以拒绝、误判或索取代价，不能只负责提示、治愈或递交信息。",
        ),
        required_event_count=len(refs),
        source_refs=tuple(refs),
    )


def render_narrative_integrity(policy: NarrativeIntegrityPolicy) -> str:
    return "\n".join(
        [
            "【叙事可信度底线｜所有类型通用】",
            *(f"- {rule}" for rule in policy.rules),
            "既定事实和硬事件边界仍以 Prompt 的硬约束区为准。",
        ]
    )


def narrative_integrity_hash(policy: NarrativeIntegrityPolicy) -> str:
    payload = json.dumps(
        policy.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compile_world_pressure_contract(preset: str) -> WorldPressureContract | None:
    """Compile an explicit setting preset; never infer a preset from prose."""
    if preset != "modern_urban_realism":
        return None
    return WorldPressureContract(
        version=WORLD_PRESSURE_VERSION,
        preset=preset,
        institutional_rules=(
            "辞职、签约、付款、报警等正式行为，只有送达正确对象或系统并产生反馈后才算完成；草稿、自发邮件或口头念头只算未完成动作。",
            "职业状态变化要保留交接、收入、社保、主管或合作方反应等至少一项直接后果，不可从决定瞬间跳到后果已经消失。",
        ),
        business_rules=(
            "营业日、生产时间、库存和开门状态是硬事实；非营业时间出现生产、香气或人员时，正文必须给出可观察原因。",
            "店铺、书店和工作室有经营、安全与隐私边界，不会只为主角需要而始终开放或免费提供信息。",
        ),
        space_time_rules=(
            "时间只能向前推进；倒叙必须明确标记。营业时间、交通停运、距离、车程和费用应改变人物选择。",
            "人物离开某地点后，不得无过渡回到更早时刻或重演刚结束的互动。",
        ),
        social_boundary_rules=(
            "拍摄、记录或公开陌生人与私人工作空间需要询问或面对拒绝、误解和使用范围；沉默不能自动视为同意。",
            "陌生人不知道主角的主题和需要，不会自动递出线索、安慰或象征性物品。",
        ),
        material_consequence_rules=(
            "钱、物品、杯子、钥匙、工牌、相机和文件的位置与归属持续有效；拿走、遗留或损坏后必须在后文承接。",
            "世界设定至少应对一个关键行动形成阻力、机会或代价，不只作为气味、灯光和地名装饰。",
        ),
    )


def render_world_pressure_contract(contract: WorldPressureContract) -> str:
    groups = (
        ("制度生效", contract.institutional_rules),
        ("经营规则", contract.business_rules),
        ("时空约束", contract.space_time_rules),
        ("社会边界", contract.social_boundary_rules),
        ("物质后果", contract.material_consequence_rules),
    )
    lines = ["【世界因果压力｜现代都市现实预设】"]
    for label, rules in groups:
        lines.append(f"{label}：")
        lines.extend(f"- {rule}" for rule in rules)
    lines.append("只使用已有设定能够支持的约束；不要为了满足本契约临时发明新制度或背景事实。")
    return "\n".join(lines)


def world_pressure_hash(contract: WorldPressureContract) -> str:
    payload = json.dumps(
        contract.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compose_narrative_control_context(
    *,
    integrity_context: str,
    integrity_mode: str,
    genre_context: str,
    genre_mode: str,
    style_context: str,
) -> str:
    """Compose active controls in credibility -> genre -> style order."""
    parts: list[str] = []
    if integrity_mode == "canary" and integrity_context:
        parts.append(integrity_context)
    if genre_mode == "canary" and genre_context:
        parts.append(genre_context)
    if style_context:
        parts.append(style_context)
    return "\n\n".join(parts)
