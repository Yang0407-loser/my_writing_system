"""Compile the minimal commercial web-fiction narrative harness.

The harness deliberately does not score or rewrite prose.  It compiles a
small, deterministic instruction block for Writer and supports shadow mode so
we can observe the contract without changing the production prompt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Iterable, Mapping


HARNESS_VERSION = "commercial-narrative-harness-v0.1"

_ACTION_MARKERS = (
    "逃", "追", "战", "冲", "袭", "围", "躲", "闯", "搏", "赶", "杀", "拦", "夺",
)
_DIALOGUE_MARKERS = (
    "争", "质问", "交涉", "对峙", "证据", "秘密", "揭露", "谈判", "试探", "回答", "拒绝",
)


@dataclass(frozen=True)
class SceneClassification:
    mode: str
    action_markers: tuple[str, ...]
    dialogue_markers: tuple[str, ...]


@dataclass(frozen=True)
class CommercialNarrativeHarness:
    version: str
    scene_mode: str
    commercial_strategy: tuple[str, ...]
    scene_contract: tuple[str, ...]
    required_event_count: int
    source_refs: tuple[dict[str, str], ...]
    classification_evidence: dict[str, tuple[str, ...]]

    def to_dict(self) -> dict:
        return asdict(self)


def classify_scene(scene_text: str) -> SceneClassification:
    """Conservatively classify a scene; ties intentionally fall back to general."""
    text = str(scene_text or "")
    action = tuple(marker for marker in _ACTION_MARKERS if marker in text)
    dialogue = tuple(marker for marker in _DIALOGUE_MARKERS if marker in text)
    if len(action) > len(dialogue) and action:
        mode = "action_pressure"
    elif len(dialogue) > len(action) and dialogue:
        mode = "dialogue_conflict"
    else:
        mode = "general"
    return SceneClassification(mode, action, dialogue)


def compile_commercial_narrative_harness(
    *,
    scene_text: str,
    required_events: Iterable[Mapping[str, object]] = (),
) -> CommercialNarrativeHarness:
    classification = classify_scene(scene_text)
    refs: list[dict[str, str]] = []
    for item in required_events or ():
        source_id = str(item.get("source_id", "")).strip()
        text_hash = str(item.get("text_hash", "")).strip()
        if source_id or text_hash:
            refs.append({"source_id": source_id, "text_hash": text_hash})

    strategy = (
        "尽快让人物接触本节问题；铺垫只保留会影响选择或后果的部分。",
        "由人物的选择推动局面，选择必须带来阻力、代价、信息变化或关系变化。",
        "本节至少形成一次可观察的故事状态变化，并给下一步行动留下压力。",
    )
    scene_contracts = {
        "general": (
            "在场景内完成硬约束事件，不按清单顺序逐项交差。",
            "用动作、对白或可观察后果承载关键信息。",
        ),
        "dialogue_conflict": (
            "每轮关键对白都应改变筹码、认知、关系或决定；删去只重复立场的来回。",
            "不靠突然打斗制造虚假推进；以一句回答、拒绝、证据或决定造成具体局面变化。",
        ),
        "action_pressure": (
            "动作必须受到空间障碍、时间压力或代价约束，不能只是连续动作词。",
            "信息通过人物可观察到的异常与即时反应揭露，动作结束后不复盘紧张感。",
        ),
    }
    return CommercialNarrativeHarness(
        version=HARNESS_VERSION,
        scene_mode=classification.mode,
        commercial_strategy=strategy,
        scene_contract=scene_contracts[classification.mode],
        required_event_count=len(refs),
        source_refs=tuple(refs),
        classification_evidence={
            "action_markers": classification.action_markers,
            "dialogue_markers": classification.dialogue_markers,
        },
    )


def render_commercial_narrative_harness(
    harness: CommercialNarrativeHarness,
) -> str:
    lines = [
        "【商业网文叙事执行策略｜当前小节】",
        f"场景策略：{harness.scene_mode}；硬事件来源：{harness.required_event_count} 项。",
        "商业推进：",
        *(f"- {item}" for item in harness.commercial_strategy),
        "场景执行：",
        *(f"- {item}" for item in harness.scene_contract),
        "硬事件内容与事实边界以 Prompt 中现有的硬约束区为准。",
    ]
    return "\n".join(lines)


def harness_hash(harness: CommercialNarrativeHarness) -> str:
    payload = json.dumps(
        harness.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
