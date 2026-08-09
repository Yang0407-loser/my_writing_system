"""Faithful reproduction of the production Writer prompt construction.

The goal is byte-level parity with the prompt the production Writer would have
built for the original task inputs, with exactly one controlled difference:
the frozen Scene Reality Contract is injected as the first block of the hard
constraint area (narrative_integrity_constraints), before any soft style /
commercial / writing-example content.

This module never touches the production Writer; it reuses the production
PromptBuilder and the same deterministic field builders.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from app.agents.character_formatter import CharacterFormatter
from app.agents.writer import (
    Writer,
    _narrative_density_instruction,
)
from app.config import settings
from app.rule_store import build_rules_context
from app.utils.style_brief import StyleSummarizer
from app.utils.style_mapping import build_style_examples
from app.writing.contracts import SubsectionInput
from app.writing.narrative_integrity import (
    compile_narrative_integrity,
    compile_world_pressure_contract,
    render_narrative_integrity,
    render_world_pressure_contract,
)
from app.writing.prompt_builder import PromptBuilder

from .inputs import ExperimentInputs
from .contract import render_scene_reality_contract_v01


def reference_passages_from_text(reference_text: str) -> str:
    """Replicate Writer.run()'s few-shot paragraph selection (writer.py:338-363)."""
    if not reference_text or not reference_text.strip():
        return ""
    paras = re.split(r"\n{2,}", reference_text.strip())
    paras = [p.strip() for p in paras if len(p.strip()) > 80]
    selected: list[str] = []
    n = len(paras)
    if n > 0:
        indices: set[int] = set()
        indices.add(0)
        if n > 1:
            indices.add(n - 1)
        if n > 3:
            indices.add(n // 2)
        for i in [2, n - 2, n // 4, 3 * n // 4]:
            if 0 <= i < n and len(indices) < 5:
                indices.add(i)
        selected = [paras[i] for i in sorted(indices)]
    if not selected:
        return ""
    out = "## 风格参考原文（请模仿以下段落的句法节奏、对话风格和用词习惯，照猫画虎）\n\n"
    for i, p in enumerate(selected, 1):
        out += f"### 参考段落 {i}\n{p}\n\n"
    return out


def _rhythm_label(intensity: int) -> str:
    if intensity <= 4:
        return "铺垫/过渡"
    if intensity <= 6:
        return "日常/推进"
    if intensity <= 8:
        return "冲突/升温"
    return "高潮/爆发"


def _build_beat_reminder(inputs: ExperimentInputs, section: int, sub_num: int) -> str:
    beat = next(
        (
            b
            for b in inputs.narrative_beats
            if b.get("section") == section and b.get("subsection") == sub_num
        ),
        None,
    )
    if not beat:
        return ""
    intensity = int(beat.get("intensity", 5))
    focus = str(beat.get("character_focus", ""))
    reminder = (
        f"【叙事节奏】本节在故事弧线中的位置: {intensity}/10 ({_rhythm_label(intensity)})。"
        f"这影响的是事件密度和张力走向，而非用词风格。"
    )
    if focus:
        reminder += f" 本节的叙事重心是: {focus}。"
    return reminder


def _format_ranked_events(inputs: ExperimentInputs, section: int, sub_num: int) -> str:
    relevant = [
        e
        for e in inputs.events
        if e.get("section") == section and e.get("subsection") == sub_num
    ]
    if not relevant:
        return "（无特殊事件）"
    lines = []
    for index, e in enumerate(relevant, 1):
        weight = int(e.get("weight", 5))
        lines.append(f"{index}. [weight={weight}] {e.get('description', '')}")
    return "\n".join(lines)


def _world_facts_str(inputs: ExperimentInputs) -> str:
    if not inputs.world_facts:
        return "（无）"
    return "\n".join(
        f"- [{f.get('category', '')}] {f.get('fact', '')}"
        for f in inputs.world_facts
    )


def _handover_context(
    inputs: ExperimentInputs, section: int, sub_num: int
) -> str:
    if sub_num == 1:
        return "（这是第一节，无前文交接笔记）"
    prev = inputs.subsection(section, sub_num - 1)
    kp = prev.get("key_points") or []
    desc = prev.get("description", "")
    summary = "、".join(kp) if kp else desc
    return f"上一小节「{prev.get('title', '')}」已完成。承接要点：{summary or '（无）'}"


def _retrieved_context(inputs: ExperimentInputs, prev_b_texts: list[str]) -> str:
    if not prev_b_texts:
        return "（无相关段落）"
    parts = []
    for i, text in enumerate(prev_b_texts, 1):
        parts.append(f"### 参考 {i}：前面已写小节\n{text}")
    return "已写段落参考（供风格和情节参照）：\n" + "\n".join(parts)


def _style_examples_context(inputs: ExperimentInputs) -> str:
    refs = reference_passages_from_text(inputs.reference_text)
    examples = build_style_examples(inputs.style)
    return "\n".join(part for part in (refs, examples) if part).strip()


def _production_integrity_blocks(inputs: ExperimentInputs) -> str:
    """The production canary blocks that were active in the baseline run."""
    required_events = []
    sec = inputs.sections[0]
    for sub in sec.get("subsections", []):
        for index, kp in enumerate(sub.get("key_points", []), 1):
            required_events.append(
                {
                    "source_id": f"outline:S1.{sub.get('subsection')}:key_point:{index}",
                    "text": str(kp),
                    "text_hash": hashlib.sha256(str(kp).encode("utf-8")).hexdigest(),
                }
            )
        if sub.get("description"):
            required_events.append(
                {
                    "source_id": f"outline:S1.{sub.get('subsection')}:description",
                    "text": str(sub.get("description")),
                    "text_hash": hashlib.sha256(
                        str(sub.get("description")).encode("utf-8")
                    ).hexdigest(),
                }
            )
    parts: list[str] = []
    if settings.WRITER_NARRATIVE_INTEGRITY_MODE == "canary":
        integrity = compile_narrative_integrity(required_events=required_events)
        parts.append(render_narrative_integrity(integrity))
    if settings.WRITER_WORLD_PRESSURE_MODE == "canary":
        world = compile_world_pressure_contract(settings.WRITER_WORLD_PRESSURE_PRESET)
        if world is not None:
            parts.append(render_world_pressure_contract(world))
    return "\n\n".join(parts)


def build_prompt_values(
    inputs: ExperimentInputs,
    *,
    section: int,
    sub_num: int,
    prev_b_texts: list[str],
    contract_text: str,
    rules_context_override: str = "",
) -> dict:
    """Reproduce Writer.run()'s prompt_values for one subsection + contract."""
    sec = next(s for s in inputs.outline if s.get("section") == section)
    sub = next(
        s for s in sec.get("subsections", []) if s.get("subsection") == sub_num
    )
    key_points = list(sub.get("key_points") or [])
    if not key_points and not sub.get("description"):
        title = sub.get("title", "")
        if title and title not in ("新节点", "新章", "新卷"):
            key_points = [title]
    target_words = sub.get("target_words", 2000)
    sub_desc = sub.get("description", "")
    section_key_points = list(sec.get("key_points") or [])
    section_title = sec.get("title", "")

    rules_context = (
        rules_context_override
        if rules_context_override
        else (inputs.rules_context or build_rules_context())
    )
    if not rules_context:
        rules_context = ""

    style = inputs.style
    density_instruction = _narrative_density_instruction(
        float(style.get("narrative_density", 0.7)) if isinstance(style, dict) else 0.7
    )
    style_structured = (
        StyleSummarizer.for_writer(style) if isinstance(style, dict) else ""
    )

    section_outline = (
        f"第{section}节「{section_title}」"
        f"—— 要点：{'、'.join(section_key_points)}"
    )

    progress_context = Writer._build_progress_context(
        inputs.outline,
        section,
        sub_num,
        len(sec.get("subsections", [])),
        key_points=key_points,
        sub_desc=sub_desc,
    )

    mandatory_events = Writer._build_mandatory_events(
        key_points=key_points,
        section_key_points=section_key_points,
        sub_desc=sub_desc,
        section_num=section,
        sub_num=sub_num,
    )
    character_constraints = Writer._build_character_constraints(inputs.characters)
    beat_reminder = _build_beat_reminder(inputs, section, sub_num)

    character_context = CharacterFormatter.build_context(
        inputs.characters, inputs.character_arcs
    )
    arc_context = CharacterFormatter.build_arc_context(
        inputs.characters, inputs.character_arcs, section=section, subsection=sub_num
    )

    production_integrity = _production_integrity_blocks(inputs)
    if contract_text:
        integrity_constraints = f"{contract_text}"
        if production_integrity:
            integrity_constraints += "\n\n" + production_integrity
    else:
        integrity_constraints = production_integrity

    values = {
        "mandatory_events": mandatory_events,
        "character_constraints": character_constraints,
        "style_constraints": "",
        "narrative_integrity_constraints": integrity_constraints,
        "progress_context": progress_context,
        "rules_context": rules_context,
        "topic": inputs.topic,
        "section": section,
        "subsection": sub_num,
        "subsection_title": sub.get("title", ""),
        "section_outline": section_outline,
        "key_points": "、".join(key_points),
        "sub_description": sub_desc if sub_desc else "（按大意自由发挥）",
        "world_setting": inputs.world_setting,
        "world_facts": _world_facts_str(inputs),
        "world_contradictions": "（无）",
        "style_structured": style_structured,
        "narrative_density_instruction": density_instruction,
        "ranked_events": _format_ranked_events(inputs, section, sub_num),
        "emotion_intensity": (
            style.get("emotion_intensity", 50) if isinstance(style, dict) else 50
        ),
        "sentence_preference": (
            style.get("sentence_preference", "balanced") if isinstance(style, dict) else "balanced"
        ),
        "sensory_density": (
            style.get("sensory_density", "medium") if isinstance(style, dict) else "medium"
        ),
        "dialogue_ratio": int(
            (style.get("dialogue_ratio", 0.2) if isinstance(style, dict) else 0.2) * 100
        ),
        "character_context": character_context,
        "arc_context": arc_context,
        "handover_context": _handover_context(inputs, section, sub_num),
        "summary_context": (
            "（故事开头）" if sub_num == 1 else "（承接上一小节）"
        ),
        "retrieved_context": _retrieved_context(inputs, prev_b_texts),
        "target_words": target_words,
        "beat_reminder": beat_reminder,
        "style_examples": _style_examples_context(inputs),
        # Keep this historical prompt reproduction compatible with the current
        # production template without changing the experiment's prompt semantics.
        "anti_ai_expression_constraints": "",
    }
    return values


def build_v01_prompt_values(
    inputs: ExperimentInputs,
    *,
    section: int,
    sub_num: int,
    prev_b_texts: list[str],
    rules_context_override: str = "",
) -> dict:
    """Build the zero-call v0.1 prompt with a subsection-scoped contract."""
    return build_prompt_values(
        inputs,
        section=section,
        sub_num=sub_num,
        prev_b_texts=prev_b_texts,
        contract_text=render_scene_reality_contract_v01(sub_num),
        rules_context_override=rules_context_override,
    )


def call_max_tokens_for(target_words: int) -> int:
    return min(
        max(settings.WRITER_MAX_TOKENS_FLOOR, int(target_words) * 4),
        settings.WRITER_MAX_TOKENS_CEIL,
    )


def build_prompt_artifact(
    inputs: ExperimentInputs,
    values: dict,
    *,
    section: int,
    sub_num: int,
    task_id: str,
    target_words: int,
) -> object:
    """Render the production prompt for the values (PromptBuilder path)."""
    prompt_values = dict(values)
    source_manifest = [
        {
            "source_id": f"writer-field:{field}",
            "field": field,
            "text_hash": hashlib.sha256(str(value).encode("utf-8")).hexdigest(),
        }
        for field, value in prompt_values.items()
    ]
    prepared = SubsectionInput(
        task_id=task_id,
        section=section,
        subsection=sub_num,
        outline_target=f"第{section}节: {prompt_values['sub_description'][:60]}",
        target_words=target_words,
        generation_settings={
            "max_tokens": call_max_tokens_for(target_words),
            "temperature": 0.5,
            "top_p": 0.9,
        },
        prepared_context_fields=prompt_values,
        source_manifest=source_manifest,
    )
    return PromptBuilder().build(prepared)


def render_user_prompt(values: dict) -> str:
    """Render the user prompt exactly as the template would."""
    from app.utils.prompt_templates import (
        WRITING_PROMPT,
        WRITING_SECTION1_PROMPT,
    )
    section = int(values["section"])
    sub_num = int(values["subsection"])
    template = (
        WRITING_SECTION1_PROMPT if (section, sub_num) == (1, 1) else WRITING_PROMPT
    )
    return template.format(**values)
