"""Deterministic outline density and word-budget advice.

The advisor is deliberately read-only and provisional.  It explains planning
pressure before Writer runs; it does not mutate an outline or infer story facts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field


ADVISOR_VERSION = "outline-budget-advisor-v1"

Confidence = Literal["low", "medium", "high"]
RecommendedAction = Literal[
    "keep", "increase", "split", "reduce_scope", "review_structure"
]


class SourceRef(BaseModel):
    source_id: str
    source_hash: str


class OutlineEventUnit(BaseModel):
    unit_id: str
    source_id: str
    source_hash: str
    unit_type: str
    text_hash: str
    actors: list[str] = Field(default_factory=list)
    time_anchor: str | None = None
    location_anchor: str | None = None
    confidence: Confidence
    extraction_reason: str


class SubsectionBudgetAdvice(BaseModel):
    section: int
    subsection: int
    current_target: int
    recommended_min: int
    recommended_preferred: int
    recommended_max: int
    event_unit_count: int
    time_jump_count: int
    scene_change_count: int
    actor_count: int
    characters_per_event_unit: float
    density_level: Literal["light", "balanced", "dense", "overloaded"]
    recommended_action: RecommendedAction
    reason_codes: list[str] = Field(default_factory=list)
    prompt_conflicts: list[str] = Field(default_factory=list)
    confidence: Confidence
    source_manifest: list[SourceRef] = Field(default_factory=list)
    event_units: list[OutlineEventUnit] = Field(default_factory=list)
    chapter_allocated_target: int | None = None
    style_factor: float = 1.0
    emotion_intensity_observed: float | None = None
    advisor_version: str = ADVISOR_VERSION


class ChapterBudgetAdvice(BaseModel):
    section: int
    chapter_budget: int
    allocated_total: int
    recommended_min_total: int
    recommended_preferred_total: int
    chapter_overconstrained: bool
    subsections: list[SubsectionBudgetAdvice]


class OutlineBudgetAdviceResult(BaseModel):
    advisor_version: str = ADVISOR_VERSION
    provisional_advisory: bool = True
    chapters: list[ChapterBudgetAdvice]


class _ExtractedUnit(BaseModel):
    text: str
    source_id: str
    source_hash: str
    unit_type: str
    confidence: Confidence
    extraction_reason: str


_MAJOR_SPLIT_RE = re.compile(r"[。！？；\n]+")
_SPACE_RE = re.compile(r"\s+")
_TIME_PATTERNS = (
    re.compile(r"第[一二三四五六七八九十\d]+个?(?:周[一二三四五六日天]|星期[一二三四五六日天])"),
    re.compile(r"(?:周|星期)[一二三四五六日天]"),
    re.compile(r"(?:凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|深夜)\s*\d{0,2}点?"),
    re.compile(r"(?:当天|当晚|次日|翌日|第二天|隔天|数日后|几天后|下周|随后|之后|后来)"),
)
_LOCATION_RE = re.compile(
    r"(?:在|到|回到|走进|离开|站在)([\u4e00-\u9fff]{0,8}(?:面包店|书店|医院|公司|家中|家里|门口|社区|街道|车站|厨房|办公室|店里|店外))"
)
_INTERACTION_MARKERS = (
    "邀请", "回答", "回应", "发问", "询问", "递给", "交谈", "交流", "相遇",
    "偶遇", "对话", "告诉", "拒绝", "答应", "承诺", "道谢",
)
_TRANSITION_MARKERS = (
    "辞职", "离职", "决定", "确立", "完成", "死亡", "离开", "住院", "负债",
    "承诺", "分手", "结婚", "搬家", "失去", "获得", "成为", "放弃",
)
_ACTION_MARKERS = _INTERACTION_MARKERS + _TRANSITION_MARKERS + (
    "收到", "写下", "拍下", "看见", "走出", "回到", "收拾", "记录", "反思",
    "观察", "打开", "关闭", "开始", "结束", "发现", "听见", "闻到", "遇见",
)


def _normalise(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", _SPACE_RE.sub("", str(text or ""))).lower()


def _hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_hash(source_id: str, text: str) -> str:
    return _hash({"source_id": source_id, "text": text})


def _round_50(value: float) -> int:
    return max(50, int(math.floor((value + 25) / 50) * 50))


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _anchors(text: str) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    for pattern in _TIME_PATTERNS:
        matches.extend((match.start(), match.end(), match.group(0)) for match in pattern.finditer(text))
    # Prefer the most specific match when patterns overlap (e.g. "第一个周六"
    # must not also count its inner "周六" as a second anchor).
    selected: list[tuple[int, int, str]] = []
    for start, end, value in sorted(matches, key=lambda item: (-(item[1] - item[0]), item[0])):
        if any(start < kept_end and end > kept_start for kept_start, kept_end, _ in selected):
            continue
        selected.append((start, end, value))
    return _unique(value for _, _, value in sorted(selected))


def _location(text: str) -> str | None:
    match = _LOCATION_RE.search(text)
    return match.group(1) if match else None


def _contains_action(text: str) -> bool:
    return any(marker in text for marker in _ACTION_MARKERS)


def _is_broad_key_point(text: str, actors: list[str]) -> bool:
    normalised = _normalise(text)
    return len(normalised) < 12 or (not actors and not _contains_action(text))


def _style_factor(style: dict) -> tuple[float, float | None]:
    factor = 1.0
    sentence = style.get("sentence_preference")
    sensory = style.get("sensory_density")
    dialogue = style.get("dialogue_ratio")
    emotion = style.get("emotion_intensity")
    if sentence == "long":
        factor += 0.05
    elif sentence == "short":
        factor -= 0.05
    if sensory == "rich":
        factor += 0.10
    elif sensory == "sparse":
        factor -= 0.05
    try:
        if float(dialogue) > 0.35:
            factor += 0.05
    except (TypeError, ValueError):
        pass
    try:
        observed = float(emotion) if emotion is not None else None
    except (TypeError, ValueError):
        observed = None
    return min(1.15, max(0.90, factor)), observed


def allocate_largest_remainder(total: int, weights: list[int]) -> list[int]:
    """Allocate exactly ``total`` using stable largest remainder ordering."""
    if total < 0:
        raise ValueError("chapter budget must be non-negative")
    if not weights:
        return []
    safe = [max(0, int(weight)) for weight in weights]
    weight_sum = sum(safe)
    if weight_sum == 0:
        safe = [1] * len(weights)
        weight_sum = len(weights)
    quotas = [total * weight / weight_sum for weight in safe]
    allocated = [math.floor(quota) for quota in quotas]
    remainder = total - sum(allocated)
    order = sorted(range(len(weights)), key=lambda idx: (-(quotas[idx] - allocated[idx]), idx))
    for idx in order[:remainder]:
        allocated[idx] += 1
    return allocated


class OutlineBudgetAdvisor:
    def extract_units(
        self,
        *,
        section: int,
        subsection: int,
        source_id: str,
        description: str,
        key_points: list[str],
        character_names: list[str],
    ) -> list[OutlineEventUnit]:
        extracted: list[_ExtractedUnit] = []
        seen: set[str] = set()

        def add(text: str, kind: str, confidence: Confidence, reason: str, sid: str) -> None:
            cleaned = text.strip(" ，,。；;\n\t")
            normalised = _normalise(cleaned)
            if not normalised or normalised in seen:
                return
            seen.add(normalised)
            extracted.append(_ExtractedUnit(
                text=cleaned,
                source_id=sid,
                source_hash=_source_hash(sid, cleaned),
                unit_type=kind,
                confidence=confidence,
                extraction_reason=reason,
            ))

        clean_points = [str(point).strip() for point in key_points if str(point).strip()]
        if clean_points:
            for idx, point in enumerate(clean_points, 1):
                point_actors = [name for name in character_names if name and name in point]
                interaction = len(point_actors) >= 2 and any(marker in point for marker in _INTERACTION_MARKERS)
                confidence: Confidence = "medium" if _is_broad_key_point(point, point_actors) else "high"
                add(
                    point,
                    "interaction_chain" if interaction else "key_point",
                    confidence,
                    "explicit_key_point",
                    f"{source_id}:key_point:{idx}",
                )
        else:
            for idx, beat in enumerate(_MAJOR_SPLIT_RE.split(description), 1):
                if _contains_action(beat):
                    beat_actors = [name for name in character_names if name and name in beat]
                    interaction = len(beat_actors) >= 2 and any(marker in beat for marker in _INTERACTION_MARKERS)
                    add(
                        beat,
                        "interaction_chain" if interaction else "description_beat",
                        "medium",
                        "description_fallback_action_chain",
                        f"{source_id}:description:{idx}",
                    )

        units: list[OutlineEventUnit] = []
        for idx, unit in enumerate(extracted, 1):
            actors = [name for name in character_names if name and name in unit.text]
            anchors = _anchors(unit.text)
            units.append(OutlineEventUnit(
                unit_id=_hash({
                    "section": section,
                    "subsection": subsection,
                    "index": idx,
                    "source_id": unit.source_id,
                    "text_hash": _hash(unit.text),
                })[:20],
                source_id=unit.source_id,
                source_hash=unit.source_hash,
                unit_type=unit.unit_type,
                text_hash=_hash(unit.text),
                actors=actors,
                time_anchor=anchors[0] if anchors else None,
                location_anchor=_location(unit.text),
                confidence=unit.confidence,
                extraction_reason=unit.extraction_reason,
            ))
        return units

    def advise_subsection(
        self,
        *,
        section: int,
        subsection: int,
        sub: dict,
        style_profile: dict,
        character_names: list[str],
        style_brief: str = "",
    ) -> SubsectionBudgetAdvice:
        source_id = str(sub.get("source_id") or sub.get("id") or f"outline:{section}:{subsection}")
        description = str(sub.get("description") or "")
        key_points = list(sub.get("key_points") or [])
        units = self.extract_units(
            section=section,
            subsection=subsection,
            source_id=source_id,
            description=description,
            key_points=key_points,
            character_names=character_names,
        )
        description_anchors = _anchors(description)
        unit_anchors = [unit.time_anchor for unit in units if unit.time_anchor]
        time_anchors = _unique(description_anchors + unit_anchors)
        locations = _unique(unit.location_anchor for unit in units if unit.location_anchor)
        actors = _unique(actor for unit in units for actor in unit.actors)
        interaction_count = sum(unit.unit_type == "interaction_chain" for unit in units)
        transition_count = sum(
            any(marker in text for marker in _TRANSITION_MARKERS)
            for text in ([description] if not key_points else key_points)
        )
        ordinal_anchors = [anchor for anchor in time_anchors if anchor.startswith("第")]
        # A numbered sequence is the clearest available timeline.  Generic
        # clock/day words inside the same sequence describe its scenes rather
        # than additional jumps.
        time_jumps = max(0, len(ordinal_anchors) - 1) if len(ordinal_anchors) >= 2 else max(0, len(time_anchors) - 1)
        scene_changes = max(0, len(locations) - 1)
        event_count = len(units)
        raw = (
            event_count * 180
            + time_jumps * 100
            + scene_changes * 100
            + max(0, len(actors) - 2) * 80
            + interaction_count * 120
            + transition_count * 150
        )
        factor, emotion = _style_factor(style_profile)
        preferred = _round_50(max(180, raw) * factor)
        recommended_min = _round_50(preferred * 0.85)
        recommended_max = _round_50(preferred * 1.15)
        current_target = max(0, int(sub.get("target_words") or 0))
        per_unit = round(current_target / event_count, 1) if event_count else 0.0

        low_structure_confidence = not key_points and len(_normalise(description)) >= 80
        broad_points = bool(key_points) and sum(unit.confidence != "high" for unit in units) >= max(2, len(units) // 2)
        confidence: Confidence
        if not units:
            confidence = "low"
        elif low_structure_confidence or broad_points:
            confidence = "medium"
        elif all(unit.confidence == "high" for unit in units):
            confidence = "high"
        else:
            confidence = "medium"

        reasons: list[str] = []
        if event_count > 5:
            reasons.append("event_units_over_5")
        if time_jumps:
            reasons.append("explicit_time_progression")
        if scene_changes:
            reasons.append("explicit_scene_changes")
        if len(actors) > 2:
            reasons.append("multi_actor_coordination")
        if interaction_count:
            reasons.append("complete_interaction_chain")
        if transition_count:
            reasons.append("persistent_state_or_decision")
        if low_structure_confidence:
            reasons.append("description_only_structure")
        if broad_points:
            reasons.append("broad_key_points")
        if not units:
            reasons.append("no_event_units")
        if current_target > recommended_max:
            reasons.append("current_target_above_recommended_max")

        if low_structure_confidence or broad_points or not units:
            action: RecommendedAction = "review_structure"
        elif event_count > 5 or time_jumps > 2 or scene_changes > 2:
            action = "split"
        elif current_target < recommended_min:
            action = "increase"
        elif current_target > recommended_max:
            action = "review_structure"
        else:
            action = "keep"

        if event_count > 5 or time_jumps > 2 or scene_changes > 2:
            density = "overloaded"
        elif current_target < recommended_min:
            density = "dense"
        elif current_target > recommended_max:
            density = "light"
        else:
            density = "balanced"

        prompt_conflicts: list[str] = []
        if re.search(r"每段[^。；\n]{0,16}(?:约|大约|保持在)?\s*[一二三四五六七八九十百千万\d]+\s*字", style_brief):
            prompt_conflicts.append("paragraph_length_instruction_may_conflict_with_subsection_total")

        manifest_by_id = {
            unit.source_id: SourceRef(source_id=unit.source_id, source_hash=unit.source_hash)
            for unit in units
        }
        return SubsectionBudgetAdvice(
            section=section,
            subsection=subsection,
            current_target=current_target,
            recommended_min=recommended_min,
            recommended_preferred=preferred,
            recommended_max=recommended_max,
            event_unit_count=event_count,
            time_jump_count=time_jumps,
            scene_change_count=scene_changes,
            actor_count=len(actors),
            characters_per_event_unit=per_unit,
            density_level=density,
            recommended_action=action,
            reason_codes=reasons[:6],
            prompt_conflicts=prompt_conflicts,
            confidence=confidence,
            source_manifest=list(manifest_by_id.values()),
            event_units=units,
            style_factor=factor,
            emotion_intensity_observed=emotion,
        )

    def advise_outline(
        self,
        *,
        outline: list[dict],
        style_profile: dict | None = None,
        character_names: list[str] | None = None,
        chapter_budget: int | None = None,
        style_brief: str = "",
    ) -> OutlineBudgetAdviceResult:
        style = style_profile or {}
        names = _unique(character_names or [])
        chapters: list[ChapterBudgetAdvice] = []
        for section_index, section in enumerate(outline, 1):
            section_number = int(section.get("section") or section_index)
            subs = list(section.get("subsections") or [])
            advice = [
                self.advise_subsection(
                    section=section_number,
                    subsection=int(sub.get("subsection") or sub_index),
                    sub=sub,
                    style_profile=style,
                    character_names=names,
                    style_brief=style_brief,
                )
                for sub_index, sub in enumerate(subs, 1)
            ]
            budget = int(chapter_budget) if chapter_budget is not None else sum(
                item.current_target for item in advice
            )
            allocations = allocate_largest_remainder(
                budget, [item.recommended_preferred for item in advice]
            )
            overconstrained = sum(item.recommended_min for item in advice) > budget
            adjusted: list[SubsectionBudgetAdvice] = []
            for item, allocated in zip(advice, allocations):
                updates: dict = {"chapter_allocated_target": allocated}
                if overconstrained and allocated < item.recommended_min:
                    updates["recommended_action"] = "reduce_scope"
                    updates["reason_codes"] = _unique(item.reason_codes + ["chapter_overconstrained"])
                adjusted.append(item.model_copy(update=updates))
            chapters.append(ChapterBudgetAdvice(
                section=section_number,
                chapter_budget=budget,
                allocated_total=sum(allocations),
                recommended_min_total=sum(item.recommended_min for item in advice),
                recommended_preferred_total=sum(item.recommended_preferred for item in advice),
                chapter_overconstrained=overconstrained,
                subsections=adjusted,
            ))
        return OutlineBudgetAdviceResult(chapters=chapters)
