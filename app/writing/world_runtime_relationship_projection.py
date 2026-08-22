"""WR3R1 projection: WR relationship_state facts -> legacy relation context.

Deterministic v1 projection of WR committed relationship facts onto the legacy
character_relation row shape and the same relation-context text format used by
``character_relation_store.build_relation_context``.  Relationship facts use
subjects ``relationship:{character_a}:{character_b}`` and predicates
``relation_type`` / ``direction`` / ``intensity`` / ``stage`` / ``description``.
"""

from __future__ import annotations

from typing import Any


RELATIONSHIP_PROJECTION_VERSION = "world-runtime-relationship-projection-wr3r1-v1"

RELATIONSHIP_PREFIX = "relationship:"
RELATIONSHIP_PREDICATES = (
    "relation_type",
    "direction",
    "intensity",
    "stage",
    "description",
)

_DIRECTION_LABELS = {"positive": "正向", "negative": "负向", "complex": "复杂"}


def relationship_ids(subject: str) -> tuple[str, str] | None:
    """Return (character_a, character_b) for a canonical relationship subject."""
    if not subject.startswith(RELATIONSHIP_PREFIX):
        return None
    parts = subject[len(RELATIONSHIP_PREFIX):].split(":")
    if len(parts) != 2 or not all(parts):
        return None
    character_a, character_b = parts
    if character_a == character_b:
        return None
    return character_a, character_b


def project_relationships(committed) -> dict[str, Any]:
    """Project one WR commit into legacy character_relation row shapes."""
    by_subject: dict[str, dict[str, Any]] = {}
    for fact in committed.after.facts:
        if not fact.subject.startswith(RELATIONSHIP_PREFIX):
            continue
        if fact.epistemic_status != "confirmed_true":
            continue
        ids = relationship_ids(fact.subject)
        if ids is None:
            continue
        by_subject.setdefault(fact.subject, {"_ids": ids})[fact.predicate] = fact.value

    rows = []
    for subject in sorted(by_subject):
        item = by_subject[subject]
        character_a, character_b = item["_ids"]
        rows.append({
            "character_a": character_a,
            "character_b": character_b,
            "relation_type": item.get("relation_type", ""),
            "direction": item.get("direction", "positive"),
            "intensity": int(item.get("intensity", 0) or 0),
            "stages": (
                [{"stage": item["stage"], "status": "active"}]
                if item.get("stage")
                else []
            ),
            "current_stage": 0,
            "description": item.get("description", ""),
            "source": "world_runtime_canonical_state",
            "source_section": 1,
        })
    return {
        "schema_version": RELATIONSHIP_PROJECTION_VERSION,
        "commit_id": committed.commit_id,
        "relations": rows,
        "coverage": {
            "relationship_count": len(rows),
            "predicates_covered": sorted({
                predicate
                for item in by_subject.values()
                for predicate in RELATIONSHIP_PREDICATES
                if item.get(predicate) is not None
            }),
            "status": "projected_from_wr" if rows else "absent",
        },
    }


def render_relation_context(relations: list[dict[str, Any]]) -> str:
    """Render the same legacy relation-context text as build_relation_context."""
    if not relations:
        return ""
    lines = []
    for relation in relations:
        stages = relation.get("stages", [])
        current_idx = relation.get("current_stage", 0)
        stage_summary = ""
        if stages:
            parts = []
            for stage in stages:
                icon = {"done": "✓", "active": "●", "pending": "○"}.get(
                    stage.get("status", "pending"), "○"
                )
                parts.append(f"{icon}{stage.get('stage', '')}")
            stage_summary = " → ".join(parts)
        direction = _DIRECTION_LABELS.get(
            relation.get("direction", "positive"), "正向"
        )
        current_stage_name = ""
        if stages and 0 <= current_idx < len(stages):
            current_stage_name = stages[current_idx].get("stage", "")
        lines.append(
            f"【{relation['character_a']} ↔ {relation['character_b']}】"
            f"{relation.get('relation_type', '')} | {direction} | "
            f"羁绊 {relation.get('intensity', 0)}/10"
        )
        if stage_summary:
            lines.append(f"  关系弧: {stage_summary}")
        if current_stage_name:
            lines.append(f"  当前阶段: {current_stage_name}")
        if relation.get("description"):
            lines.append(f"  状态: {relation['description']}")
    return "## 角色关系状态\n" + "\n".join(lines)
