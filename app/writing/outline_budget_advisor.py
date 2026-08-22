"""Deterministic outline density and word-budget advice.

V1's provisional formula is preserved. Event interpretation is owned solely by
OutlineEventContractCompiler so downstream consumers share one traceable view.
"""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, Field

from .outline_event_contract import (
    Confidence,
    ContractSourceRef,
    OutlineEventContractCompiler,
    OutlineEventUnit,
    SubsectionEventContract,
)


ADVISOR_VERSION = "outline-budget-advisor-v1-event-contract"

RecommendedAction = Literal[
    "keep", "increase", "split", "reduce_scope", "review_structure"
]


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
    source_manifest: list[ContractSourceRef] = Field(default_factory=list)
    event_units: list[OutlineEventUnit] = Field(default_factory=list)
    event_contract: SubsectionEventContract
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


def _round_50(value: float) -> int:
    return max(50, int(math.floor((value + 25) / 50) * 50))


def _unique(values):
    return list(dict.fromkeys(value for value in values if value))


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
    order = sorted(
        range(len(weights)),
        key=lambda index: (-(quotas[index] - allocated[index]), index),
    )
    for index in order[:remainder]:
        allocated[index] += 1
    return allocated


def _time_anchor_values(events: list[OutlineEventUnit]) -> list[str]:
    values: list[str] = []
    for event in events:
        if event.time_anchor:
            values.extend(part for part in event.time_anchor.split("｜") if part)
    return _unique(values)


class OutlineBudgetAdvisor:
    def __init__(
        self, compiler: OutlineEventContractCompiler | None = None
    ) -> None:
        self.compiler = compiler or OutlineEventContractCompiler()

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
        """Compatibility facade; extraction still belongs to the contract adapter."""
        return list(self.compiler.adapter.extract(
            section=section,
            subsection=subsection,
            source_id=source_id,
            description=description,
            key_points=key_points,
            character_names=character_names,
        ))

    def advise_subsection(
        self,
        *,
        contract: SubsectionEventContract,
        style_profile: dict,
        style_brief: str = "",
    ) -> SubsectionBudgetAdvice:
        events = [
            event for event in contract.events if event.status != "superseded"
        ]
        time_anchors = _time_anchor_values(events)
        locations = _unique(
            event.location_anchor for event in events if event.location_anchor
        )
        actors = _unique(actor for event in events for actor in event.actors)
        interaction_count = sum(
            event.unit_type == "dialogue_interaction" for event in events
        )
        transition_count = sum(
            event.unit_type in {"decision", "state_transition"} for event in events
        )
        ordinal_anchors = [
            anchor for anchor in time_anchors if anchor.startswith("第")
        ]
        time_jumps = (
            max(0, len(ordinal_anchors) - 1)
            if len(ordinal_anchors) >= 2
            else max(0, len(time_anchors) - 1)
        )
        scene_changes = max(0, len(locations) - 1)
        event_count = len(events)
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
        current_target = max(0, int(contract.current_target_words))
        per_unit = round(current_target / event_count, 1) if event_count else 0.0

        description_only = bool(events) and all(
            event.source_slot.startswith("desc:") for event in events
        )
        broad_points = any(
            event.source_slot.startswith("kp:") and event.confidence == "low"
            for event in events
        )
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
        if description_only:
            reasons.append("description_only_structure")
        if broad_points:
            reasons.append("broad_key_points")
        if not events:
            reasons.append("no_event_units")
        if current_target > recommended_max:
            reasons.append("current_target_above_recommended_max")
        if contract.status == "stale":
            reasons.append("event_contract_stale")

        if contract.confidence == "low" or contract.status == "stale":
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
        if re.search(
            r"每段[^。；\n]{0,16}(?:约|大约|保持在)?\s*"
            r"[一二三四五六七八九十百千万\d]+\s*字",
            style_brief,
        ):
            prompt_conflicts.append(
                "paragraph_length_instruction_may_conflict_with_subsection_total"
            )
        return SubsectionBudgetAdvice(
            section=contract.section,
            subsection=contract.subsection,
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
            reason_codes=reasons[:7],
            prompt_conflicts=prompt_conflicts,
            confidence=contract.confidence,
            source_manifest=list(contract.source_manifest),
            event_units=events,
            event_contract=contract,
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
            budget = (
                int(chapter_budget)
                if chapter_budget is not None
                else sum(max(0, int(sub.get("target_words") or 0)) for sub in subs)
            )
            chapter_contract = self.compiler.compile_chapter(
                section=section_number,
                subsections=subs,
                character_names=names,
                chapter_target_words=budget,
            )
            advice = [
                self.advise_subsection(
                    contract=contract,
                    style_profile=style,
                    style_brief=style_brief,
                )
                for contract in chapter_contract.subsection_contracts
            ]
            allocations = allocate_largest_remainder(
                budget, [item.recommended_preferred for item in advice]
            )
            overconstrained = (
                sum(item.recommended_min for item in advice) > budget
            )
            adjusted: list[SubsectionBudgetAdvice] = []
            for item, allocated in zip(advice, allocations):
                updates: dict = {"chapter_allocated_target": allocated}
                if overconstrained and allocated < item.recommended_min:
                    updates["recommended_action"] = "reduce_scope"
                    updates["reason_codes"] = _unique(
                        item.reason_codes + ["chapter_overconstrained"]
                    )
                adjusted.append(item.model_copy(update=updates))
            chapters.append(ChapterBudgetAdvice(
                section=section_number,
                chapter_budget=budget,
                allocated_total=sum(allocations),
                recommended_min_total=sum(item.recommended_min for item in advice),
                recommended_preferred_total=sum(
                    item.recommended_preferred for item in advice
                ),
                chapter_overconstrained=overconstrained,
                subsections=adjusted,
            ))
        return OutlineBudgetAdviceResult(chapters=chapters)
