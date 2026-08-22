"""Read-only assembly of StateFrame V1 artifacts from existing task sources."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .outline_event_contract import OutlineEventContractCompiler
from .state_frame_builder import (
    StateFrameBuilder,
    expectations_from_outline_contract,
    fact_from_mapping,
    facts_from_foreshadows,
    facts_from_handover,
    facts_from_post_write_bundle,
    facts_from_relations,
)
from .state_frame_quality import StateFrameQualityEvaluator
from .state_frame_v1 import StateSourceRef, canonical_hash


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _subsections(outline: list[dict]) -> list[tuple[int, dict]]:
    result: list[tuple[int, dict]] = []
    for section_index, chapter in enumerate(outline, 1):
        section = int(chapter.get("section") or section_index)
        children = chapter.get("subsections") or chapter.get("children") or []
        for subsection_index, subsection in enumerate(children, 1):
            if not isinstance(subsection, Mapping):
                continue
            data = dict(subsection)
            data.setdefault("subsection", subsection_index)
            result.append((section, data))
    return result


def _outline_contracts(
    outline: list[dict], characters: list[dict]
) -> list[dict]:
    names = [
        str(item.get("name") or "")
        for item in characters
        if isinstance(item, Mapping) and str(item.get("name") or "")
    ]
    compiler = OutlineEventContractCompiler()
    result: list[dict] = []
    by_section: dict[int, list[dict]] = {}
    for section, subsection in _subsections(outline):
        by_section.setdefault(section, []).append(subsection)
    for section, subsections in by_section.items():
        chapter = compiler.compile_chapter(
            section=section,
            subsections=subsections,
            character_names=names,
            chapter_target_words=sum(
                int(item.get("target_words") or 0) for item in subsections
            ),
        )
        result.extend(
            item.model_dump(mode="json") for item in chapter.subsection_contracts
        )
    return result


def _post_write_bundles(records: list[Any]) -> list[dict]:
    result = []
    for item in records:
        if not isinstance(item, Mapping):
            continue
        bundle = item.get("bundle")
        record = item.get("record") if isinstance(item.get("record"), Mapping) else {}
        if isinstance(bundle, Mapping):
            result.append(dict(bundle))
        elif record.get("status") == "completed":
            # A completed record without the private bundle cannot be used as facts.
            continue
    return sorted(
        result,
        key=lambda value: (
            int(value.get("section") or 0),
            int(value.get("subsection") or 0),
        ),
    )


def _current_character_facts(characters: list[Any]) -> list:
    facts = []
    for index, character in enumerate(characters, 1):
        if not isinstance(character, Mapping):
            continue
        name = str(character.get("name") or character.get("id") or f"character-{index}")
        state_fields = {
            key: character.get(key)
            for key in (
                "current_state", "location", "physical_state", "emotional_state",
                "current_goal", "knowledge_state", "possessions", "presence",
            )
            if character.get(key) not in (None, "", [], {})
        }
        if not state_fields:
            continue
        source_id = str(character.get("id") or f"character:{index}")
        facts.append(fact_from_mapping({
            "fact_type": "character_state",
            "subject": name,
            "predicate": "current_character_snapshot",
            "value": state_fields,
            "status": "confirmed",
            "durability": "persistent",
            "source_type": "character_state_store",
            "source_id": source_id,
            "source_hash": canonical_hash(character),
            "producer": "existing_character_state",
            "confidence": 1.0,
            "provenance": "authoritative_current_store_snapshot",
        }))
    return facts


def _manifest_for_facts(facts: list) -> list[StateSourceRef]:
    unique = {}
    for fact in facts:
        key = (fact.source_type, fact.source_id, fact.source_hash)
        unique[key] = StateSourceRef(
            source_type=fact.source_type,
            source_id=fact.source_id,
            source_hash=fact.source_hash,
            producer=fact.producer,
            section=fact.section,
            subsection=fact.subsection,
        )
    return list(unique.values())


def build_state_frame_artifacts(
    *,
    task_id: str,
    section: int,
    subsection: int,
    task_data: Mapping[str, Any] | None = None,
    checkpoint: Mapping[str, Any] | None = None,
    relations: list[Mapping[str, Any]] | None = None,
    foreshadows: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build before/after/delta without mutating any source."""
    task_data = dict(task_data or {})
    checkpoint = dict(checkpoint or {})
    outline = _list(
        task_data.get("outline")
        or checkpoint.get("outline_v2")
        or checkpoint.get("outline")
    )
    characters = _list(
        checkpoint.get("characters") or task_data.get("characters")
    )
    handovers = _list(
        checkpoint.get("handover_chain")
        or task_data.get("handover_notes")
        or task_data.get("handover_json")
    )
    records = _list(task_data.get("post_write_extraction_shadow"))
    bundles = _post_write_bundles(records)

    current_snapshot_facts = [
        *_current_character_facts(characters),
        *facts_from_relations(relations or []),
        *facts_from_foreshadows(foreshadows or []),
        *facts_from_handover(handovers, section=section),
    ]
    before_bundle_facts = []
    after_bundle_facts = []
    target_bundle_found = False
    for bundle in bundles:
        position = (
            int(bundle.get("section") or 0),
            int(bundle.get("subsection") or 0),
        )
        target = (section, subsection)
        bundle_facts = facts_from_post_write_bundle(bundle)
        if position < target:
            before_bundle_facts.extend(bundle_facts)
            after_bundle_facts.extend(bundle_facts)
        elif position == target:
            target_bundle_found = True
            after_bundle_facts.extend(bundle_facts)

    contracts = _outline_contracts(outline, characters) if outline else []
    expectations = expectations_from_outline_contract(
        contracts, section=section, subsection=subsection
    )
    unavailable = []
    if not records:
        unavailable.append("post_write_state_bundle")
    elif not target_bundle_found:
        unavailable.append("target_post_write_state_bundle")
    if not handovers:
        unavailable.append("handover")
    if not relations:
        unavailable.append("relationship_state")
    if not foreshadows:
        unavailable.append("foreshadow_state")
    # Current stores do not retain per-subsection historical snapshots.
    unavailable.append("historical_current_store_snapshots")

    builder = StateFrameBuilder()
    checkpoint_version = str(
        checkpoint.get("checkpoint_version")
        or checkpoint.get("_checkpoint_version")
        or ""
    ) or None
    checkpoint_id = (
        f"checkpoint:{task_id}:{checkpoint.get('current_section', section)}"
        if checkpoint else None
    )
    base_manifest = _manifest_for_facts(current_snapshot_facts)
    before = builder.build(
        task_id=task_id,
        section=section,
        subsection=subsection,
        frame_phase="before_generation",
        facts=[*current_snapshot_facts, *before_bundle_facts],
        expectations=expectations,
        source_manifest=[
            *base_manifest, *_manifest_for_facts(before_bundle_facts)
        ],
        unavailable_source_types=unavailable,
        checkpoint_id=checkpoint_id,
        checkpoint_version=checkpoint_version,
    )
    after = builder.build(
        task_id=task_id,
        section=section,
        subsection=subsection,
        frame_phase="after_commit",
        facts=[*current_snapshot_facts, *after_bundle_facts],
        expectations=expectations,
        source_manifest=[
            *base_manifest, *_manifest_for_facts(after_bundle_facts)
        ],
        unavailable_source_types=unavailable,
        checkpoint_id=checkpoint_id,
        checkpoint_version=checkpoint_version,
    )
    delta = builder.delta(before, after)
    quality = StateFrameQualityEvaluator().evaluate(before, after, delta)
    return {
        "before": before.model_dump(mode="json"),
        "after": after.model_dump(mode="json"),
        "delta": delta.model_dump(mode="json"),
        "quality": quality.model_dump(mode="json"),
        "production_effect": False,
        "writer_llm_calls": 0,
    }
