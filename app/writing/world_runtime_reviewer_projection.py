"""WR3.6 projection: WR commit -> legacy Reviewer context inputs.

Deterministic v1 projection that supplies the reviewer's context inputs
(handover_chain, character_consistency_context, relation_context,
subplot_context) from one WR commit.  Relation and subplot contexts have no WR
ontology source yet and are explicitly marked ``legacy_only_not_projected``.
"""

from __future__ import annotations

import json
from typing import Any

from .world_runtime_character_projection import project_characters
from .world_runtime_handover_projection import project_handover
from .world_runtime_relationship_projection import (
    project_relationships,
    render_relation_context,
)


REVIEWER_PROJECTION_VERSION = "world-runtime-reviewer-projection-wr3.6-v1"


def _character_context(characters: dict[str, Any]) -> str:
    lines = []
    for row in characters["characters"]:
        attributes = "; ".join(
            f"{item['predicate']}={item['value']}"
            for item in row["attributes"]
        )
        lines.append(f"{row['character_id']}: {attributes}")
    return "\n".join(lines) if lines else "（无角色数据）"


def project_reviewer_context(committed) -> dict[str, Any]:
    """Project one WR commit into reviewer context inputs."""
    characters = project_characters(committed)
    handover = project_handover(committed)
    relationships = project_relationships(committed)
    if relationships["relations"]:
        relation_context = render_relation_context(relationships["relations"])
        relation_context_status = "projected_from_wr"
    else:
        relation_context = "（无关系数据：WR 本体无 relationship_state，legacy_only）"
        relation_context_status = "legacy_only_not_projected"
    subplot_context = "（无支线数据：WR 本体无 subplot 类型，legacy_only）"
    return {
        "schema_version": REVIEWER_PROJECTION_VERSION,
        "commit_id": committed.commit_id,
        "handover_chain": json.dumps(handover["note"], ensure_ascii=False),
        "character_consistency_context": _character_context(characters),
        "relation_context": relation_context,
        "subplot_context": subplot_context,
        "world_review_summary": {
            "revision": committed.after.revision,
            "fact_count": len(committed.after.facts),
            "committed_changes": len(committed.ledger.entries),
            "event_only_change_types": sorted({
                entry.change_type
                for entry in committed.ledger.entries
                if entry.fact_id is None
            }),
            "created_or_changed_facts": handover["note"]["new_facts"],
            "open_threads": handover["note"]["open_threads"],
        },
        "coverage": {
            "handover_chain_status": "projected_from_wr",
            "character_consistency_context_status": "projected_from_wr",
            "relation_context_status": relation_context_status,
            "subplot_context_status": "legacy_only_not_projected",
        },
    }
