"""Versioned character-arc contracts and explicit EventGraph edge planning."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from typing import Any


CONTRACT_V1 = "v1"
CONTRACT_V2 = "v2"
VALID_CONTRACT_VERSIONS = {CONTRACT_V1, CONTRACT_V2}

HARD_ARC_TRANSITION = "hard_arc_transition"
SOFT_ARC_PROGRESS = "soft_arc_progress"
OBSERVATIONAL_TEXTURE = "observational_texture"
ORDINARY_PLOT_EVENT = "ordinary_plot_event"
UNSUPPORTED_PLANNING_INFERENCE = "unsupported_planning_inference"
UNRESOLVED = "unresolved"

VALID_CLASSIFICATIONS = {
    HARD_ARC_TRANSITION,
    SOFT_ARC_PROGRESS,
    OBSERVATIONAL_TEXTURE,
    ORDINARY_PLOT_EVENT,
    UNSUPPORTED_PLANNING_INFERENCE,
}

HARD_REQUIRED_FIELDS = (
    "before_state",
    "trigger",
    "after_state",
    "observable_evidence",
    "source_id",
    "source_hash",
    "rationale",
)


def resolve_contract_version(value: str | None) -> str:
    normalized = (value or CONTRACT_V1).strip().lower()
    return normalized if normalized in VALID_CONTRACT_VERSIONS else CONTRACT_V1


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_outline_source_manifest(outline: list[dict] | None) -> dict[tuple[int, int], dict]:
    """Build deterministic subsection provenance without consulting evaluation data."""
    manifest: dict[tuple[int, int], dict] = {}
    for section in outline or []:
        section_num = int(section.get("section", 0) or 0)
        for subsection in section.get("subsections", []) or []:
            sub_num = int(subsection.get("subsection", 0) or 0)
            if section_num <= 0 or sub_num <= 0:
                continue
            source_payload = {
                "section": section_num,
                "subsection": sub_num,
                "title": subsection.get("title", ""),
                "description": subsection.get("description", ""),
                "key_points": subsection.get("key_points", []),
            }
            manifest[(section_num, sub_num)] = {
                "source_id": f"outline:S{section_num}.{sub_num}",
                "source_hash": _canonical_hash(source_payload),
            }
    return manifest


def _milestone_id(character_id: str, milestone: dict, index: int) -> str:
    existing = str(milestone.get("milestone_id", "")).strip()
    if existing:
        return existing
    identity = {
        "character_id": character_id,
        "section": milestone.get("section", 0),
        "subsection": milestone.get("subsection", 0),
        "event": milestone.get("event", milestone.get("description", "")),
        "index": index,
    }
    return f"arc-{_canonical_hash(identity)[:16]}"


def interpret_legacy_milestone(milestone: dict) -> dict:
    """Return a non-mutating compatibility view of an unclassified milestone."""
    result = copy.deepcopy(milestone)
    if not result.get("classification"):
        result["classification"] = SOFT_ARC_PROGRESS
        result["requiredness"] = "soft"
        result["legacy_unclassified"] = True
    return result


def normalize_v2_arcs(
    arcs: list[dict] | None,
    outline: list[dict] | None,
    *,
    hard_limit_per_character_section: int = 2,
    legacy_unclassified_as_soft: bool = False,
) -> list[dict]:
    """Validate V2 milestones, attach provenance, and downgrade unsafe hard items."""
    source_manifest = build_outline_source_manifest(outline)
    normalized_arcs = copy.deepcopy(arcs or [])

    for arc in normalized_arcs:
        if not isinstance(arc, dict):
            continue
        character_id = str(arc.get("character_id", ""))
        hard_counts: dict[int, int] = defaultdict(int)
        milestones = arc.get("key_milestones", []) or []
        for index, milestone in enumerate(milestones):
            if not isinstance(milestone, dict):
                continue
            was_legacy_unclassified = not milestone.get("contract_version") and not milestone.get("classification")
            section = int(milestone.get("section", 0) or 0)
            subsection = int(milestone.get("subsection", 0) or 0)
            milestone["milestone_id"] = _milestone_id(character_id, milestone, index)
            milestone["contract_version"] = CONTRACT_V2

            provenance = source_manifest.get((section, subsection))
            if provenance:
                milestone["source_id"] = provenance["source_id"]
                milestone["source_hash"] = provenance["source_hash"]

            classification = str(milestone.get("classification", "")).strip()
            if classification not in VALID_CLASSIFICATIONS:
                if legacy_unclassified_as_soft and was_legacy_unclassified:
                    milestone["classification"] = SOFT_ARC_PROGRESS
                    milestone["requiredness"] = "soft"
                    milestone["legacy_unclassified"] = True
                    continue
                milestone["classification"] = UNRESOLVED
                milestone["requiredness"] = "unresolved"
                milestone["downgrade_reason"] = "invalid_or_missing_classification"
                continue

            milestone["classification"] = classification
            if classification == HARD_ARC_TRANSITION:
                missing = [field for field in HARD_REQUIRED_FIELDS if not milestone.get(field)]
                if missing:
                    milestone["classification"] = SOFT_ARC_PROGRESS
                    milestone["requiredness"] = "soft"
                    milestone["downgrade_reason"] = "incomplete_hard_transition"
                    milestone["missing_hard_fields"] = missing
                    continue
                if hard_counts[section] >= hard_limit_per_character_section:
                    milestone["classification"] = SOFT_ARC_PROGRESS
                    milestone["requiredness"] = "soft"
                    milestone["downgrade_reason"] = "hard_arc_limit"
                    continue
                hard_counts[section] += 1
                milestone["requiredness"] = "hard"
            elif classification == SOFT_ARC_PROGRESS:
                milestone["requiredness"] = "soft"
            else:
                milestone["requiredness"] = "non_injectable"

    return normalized_arcs


def iter_v2_event_milestones(arcs: list[dict] | None):
    """Yield only V2 milestones that may be visible as Writer event context."""
    for arc in arcs or []:
        if not isinstance(arc, dict):
            continue
        character_id = str(arc.get("character_id", ""))
        for milestone in arc.get("key_milestones", []) or []:
            if not isinstance(milestone, dict):
                continue
            if milestone.get("classification") not in {HARD_ARC_TRANSITION, SOFT_ARC_PROGRESS}:
                continue
            yield character_id, milestone


def build_v2_edge_plan(arcs: list[dict] | None) -> list[dict]:
    """Build only explicit or state-backed directed edges for V2 milestones."""
    milestones: dict[str, tuple[str, dict]] = {}
    by_character: dict[str, list[dict]] = defaultdict(list)
    for character_id, milestone in iter_v2_event_milestones(arcs):
        milestone_id = str(milestone.get("milestone_id", ""))
        if not milestone_id:
            continue
        milestones[milestone_id] = (character_id, milestone)
        by_character[character_id].append(milestone)

    edges: dict[tuple[str, str], dict] = {}
    edge_priority = {
        "ordered_hard_transition": 1,
        "explicit_dependency": 2,
        "explicit_causal": 3,
    }

    def add_edge(source_id: str, target_id: str, edge_type: str, rationale: str, rule: str):
        if source_id == target_id or source_id not in milestones or target_id not in milestones:
            return
        source = milestones[source_id][1]
        target = milestones[target_id][1]
        key = (source_id, target_id)
        existing = edges.get(key)
        if existing and edge_priority[existing["edge_type"]] >= edge_priority[edge_type]:
            return
        edges[key] = {
            "edge_type": edge_type,
            "from_milestone_id": source_id,
            "to_milestone_id": target_id,
            "rationale": rationale,
            "source_ids": list(dict.fromkeys([
                source.get("source_id", ""), target.get("source_id", "")
            ])),
            "source_hashes": list(dict.fromkeys([
                source.get("source_hash", ""), target.get("source_hash", "")
            ])),
            "construction_rule": rule,
            "contract_version": CONTRACT_V2,
        }
        edges[key]["source_ids"] = [item for item in edges[key]["source_ids"] if item]
        edges[key]["source_hashes"] = [item for item in edges[key]["source_hashes"] if item]

    for milestone_id, (_, milestone) in milestones.items():
        for dependency in milestone.get("depends_on", []) or []:
            add_edge(
                str(dependency), milestone_id, "explicit_dependency",
                str(milestone.get("dependency_rationale", "explicit milestone dependency")),
                "v2.explicit_depends_on",
            )
        for caused in milestone.get("causes", []) or []:
            add_edge(
                milestone_id, str(caused), "explicit_causal",
                str(milestone.get("causal_rationale", "explicit milestone causality")),
                "v2.explicit_causes",
            )

    for character_id, items in by_character.items():
        hard_items = [item for item in items if item.get("classification") == HARD_ARC_TRANSITION]
        hard_items.sort(key=lambda item: (
            int(item.get("section", 0) or 0),
            int(item.get("subsection", 0) or 0),
            str(item.get("milestone_id", "")),
        ))
        for previous, current in zip(hard_items, hard_items[1:]):
            after_state = str(previous.get("after_state", "")).strip()
            before_state = str(current.get("before_state", "")).strip()
            if after_state and before_state and after_state == before_state:
                add_edge(
                    str(previous["milestone_id"]), str(current["milestone_id"]),
                    "ordered_hard_transition",
                    f"{character_id}: previous after_state equals next before_state",
                    "v2.ordered_hard_state_chain",
                )

    return [edges[key] for key in sorted(edges)]


def count_legacy_link_operations(arcs: list[dict] | None) -> dict:
    """Reproduce V1 coordinator link-operation counts without creating a graph."""
    same_character = 0
    section_counts: dict[int, int] = defaultdict(int)
    milestones = 0
    for arc in arcs or []:
        items = [item for item in arc.get("key_milestones", []) or [] if isinstance(item, dict)]
        same_character += max(0, len(items) - 1)
        milestones += len(items)
        for item in items:
            section = int(item.get("section", 0) or 0)
            if section:
                section_counts[section] += 1
    same_section = sum(count * (count - 1) // 2 for count in section_counts.values())
    return {
        "milestones": milestones,
        "same_character_consecutive_links": same_character,
        "same_section_pairwise_links": same_section,
        "link_operations": same_character + same_section,
    }
