"""WR3.10+ consumer read-switch coverage audit (offline, read-only).

Uses the WR3.9+ key-level semantic matrix to audit, for each of the six WR3
consumers (Handover / WorldState / character store / Checkpoint / RAG metadata /
Reviewer), what a read switch to the WR projection would cover:

- field-level status (projected_from_wr / legacy_only_not_projected /
  additive / preserved / meta);
- the consumer's WR key footprint, annotated with whether each key has a
  legacy semantic equivalent (covered) or is WR-only;
- a deterministic switch recommendation per consumer.

Zero LLM, zero state mutation; the runner writes one frozen JSON report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.writing.world_runtime_state_committer import CommittedWorldState
from experiments.world_runtime_writer_canary.wr39_compare_key_level import (
    _legacy_frame_facts,
    _wr_frame_facts,
)
from experiments.world_runtime_writer_canary.wr39_semantic_mapping import (
    key_level_compare,
)


CONSUMER_DEFS: dict[str, dict[str, Any]] = {
    "handover": {
        "projection": "world_runtime_handover_projection",
        "field_specs": [
            {"field": "character_state", "kind": "fact_field", "status_kind": "field_coverage"},
            {"field": "open_threads", "kind": "fact_field", "status_kind": "field_coverage"},
            {"field": "new_facts", "kind": "fact_field", "status_kind": "field_coverage"},
            {"field": "foreshadowing", "kind": "legacy_only", "status_kind": "field_coverage"},
            {"field": "found_contradictions", "kind": "legacy_only", "status_kind": "field_coverage"},
            {"field": "arc_progress", "kind": "legacy_only", "status_kind": "field_coverage"},
        ],
        "wr_key_selector": "all",
        "gaps": ["handover_foreshadowing_found_contradictions_arc_progress_legacy_only"],
    },
    "world_state": {
        "projection": "project_world_state_facts",
        "field_specs": [
            {"field": "facts", "kind": "fact_all", "status_kind": "wr_facts"},
        ],
        "wr_key_selector": "all",
        "gaps": [],
    },
    "character_store": {
        "projection": "project_characters",
        "field_specs": [
            {"field": "characters", "kind": "fact_field", "status_kind": "character_rows"},
            {"field": "relations", "kind": "legacy_only", "status_kind": "coverage_suffix"},
            {"field": "factions", "kind": "legacy_only", "status_kind": "coverage_suffix"},
        ],
        "wr_key_selector": "character_subjects",
        "gaps": [
            "character_store_personality_strengths_weaknesses_key_lines_relationships_custom_legacy_only"
        ],
    },
    "rag_metadata": {
        "projection": "project_rag_metadata",
        "field_specs": [
            {"field": "characters", "kind": "fact_field", "status_kind": "coverage_suffix"},
            {"field": "time", "kind": "fact_field", "status_kind": "coverage_suffix"},
            {"field": "weekday", "kind": "fact_field", "status_kind": "coverage_suffix"},
            {"field": "locations", "kind": "fact_field", "status_kind": "coverage_suffix"},
            {"field": "world_revision", "kind": "meta", "status_kind": "meta"},
        ],
        "wr_key_selector": "rag",
        "gaps": [],
    },
    "checkpoint": {
        "projection": "world_runtime_checkpoint_shadow",
        "field_specs": [
            {"field": "world_runtime_shadow_v1", "kind": "additive", "status_kind": "additive"},
            {"field": "legacy_checkpoint_keys", "kind": "preserved", "status_kind": "preserved"},
        ],
        "wr_key_selector": "all",
        "gaps": [],
    },
    "reviewer": {
        "projection": "project_reviewer_context",
        "field_specs": [
            {"field": "handover_chain", "kind": "fact_field", "status_kind": "reviewer_field"},
            {"field": "character_consistency_context", "kind": "fact_field", "status_kind": "reviewer_field"},
            {"field": "relation_context", "kind": "legacy_only", "status_kind": "reviewer_field"},
            {"field": "subplot_context", "kind": "legacy_only", "status_kind": "reviewer_field"},
        ],
        "wr_key_selector": "all",
        "gaps": ["reviewer_relation_subplot_legacy_only_requires_side_by_side"],
    },
}


def _field_status(projection: dict[str, Any], spec: dict[str, Any]) -> str:
    kind = spec["status_kind"]
    field = spec["field"]
    if kind == "field_coverage":
        return projection["field_coverage"][field]["status"]
    if kind == "coverage_suffix":
        coverage_key = f"{field}_status"
        if coverage_key in projection["coverage"]:
            return projection["coverage"][coverage_key]
        value = projection.get("metadata", {}).get(field)
        return "projected_from_wr" if value is not None else "absent"
    if kind == "character_rows":
        return "projected_from_wr"
    if kind == "reviewer_field":
        return projection["coverage"][{
            "handover_chain": "handover_chain_status",
            "character_consistency_context": "character_consistency_context_status",
            "relation_context": "relation_context_status",
            "subplot_context": "subplot_context_status",
        }[field]]
    if kind == "wr_facts":
        return "projected_from_wr"
    if kind == "meta":
        return "meta_projected"
    if kind == "additive":
        return "additive_wr_field"
    if kind == "preserved":
        return "legacy_preserved"
    return "unknown"


def _field_item_count(projection: dict[str, Any], spec: dict[str, Any]) -> int:
    kind = spec["status_kind"]
    field = spec["field"]
    if kind == "field_coverage":
        return projection["field_coverage"][field]["item_count"]
    if kind == "wr_facts":
        return projection["count"]
    if kind == "character_rows":
        return projection["coverage"].get("character_count", 0) or 0
    if kind == "coverage_suffix":
        if field in ("relations", "factions"):
            return projection["coverage"].get(f"{field}_count", 0) or 0
        if field == "characters":
            if "character_count" in projection["coverage"]:
                return projection["coverage"]["character_count"] or 0
            return len(projection["metadata"].get("characters", []))
        if field in ("time", "weekday"):
            return 1 if projection["metadata"].get(field) is not None else 0
        if field == "locations":
            return len(projection["metadata"].get("locations", []))
        return 1
    if kind == "reviewer_field":
        # Reviewer context inputs are always strings; legacy-only fields are
        # placeholders, so count them as 0 for data-loss semantics.
        return 0 if field in ("relation_context", "subplot_context") else 1
    if kind in ("additive", "preserved", "meta"):
        return 1
    return 0


def _select_wr_keys(key_result: dict[str, Any], selector: str) -> list[dict[str, Any]]:
    rows = key_result["wr_coverage"]
    if selector == "all":
        return rows
    if selector == "character_subjects":
        return [
            row for row in rows
            if row["wr_key"][1].startswith(("character:", "employment:"))
        ]
    if selector == "rag":
        return [
            row for row in rows
            if row["wr_key"][1].startswith(("character:", "employment:"))
            or (
                row["wr_key"][1] == "world_clock"
                and row["wr_key"][2] in ("time", "weekday")
            )
        ]
    return []


def audit_consumer(
    consumer_id: str,
    projection: dict[str, Any],
    key_result: dict[str, Any],
) -> dict[str, Any]:
    """Audit one consumer against one subsection's WR projection + key matrix."""
    definition = CONSUMER_DEFS[consumer_id]
    fields = []
    for spec in definition["field_specs"]:
        fields.append({
            "field": spec["field"],
            "kind": spec["kind"],
            "status": _field_status(projection, spec),
            "item_count": _field_item_count(projection, spec),
        })
    footprint = _select_wr_keys(key_result, definition["wr_key_selector"])
    wr_only = [
        row["wr_key"] for row in footprint
        if not row["covered"]
    ]
    return {
        "consumer": consumer_id,
        "projection": definition["projection"],
        "fields": fields,
        "wr_key_footprint": [
            {
                "wr_key": row["wr_key"],
                "wr_value": row["wr_value"],
                "legacy_equivalent": row["covered"],
                "covered_by_legacy_keys": row["covered_by_legacy_keys"],
            }
            for row in footprint
        ],
        "summary": {
            "wr_key_count": len(footprint),
            "wr_only_key_count": len(wr_only),
            "wr_only_keys": sorted(wr_only),
            "gaps": definition.get("gaps", []),
        },
    }


def _recommend(
    consumer_id: str,
    fields: list[dict[str, Any]],
    wr_only: list[list[str]],
    gaps: list[str],
) -> str:
    legacy_only_with_data = [
        field for field in fields
        if field["kind"] == "legacy_only" and field["max_item_count"] > 0
    ]
    if legacy_only_with_data:
        return "blocked_legacy_only_data_loss"
    if consumer_id == "reviewer":
        return "needs_side_by_side_decision"
    if wr_only:
        return "switch_ready_accept_wr_only"
    return "switch_ready"


def aggregate_consumer(
    consumer_id: str,
    audits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate per-subsection audits into one consumer-level verdict."""
    definition = CONSUMER_DEFS[consumer_id]
    fields = []
    for spec in definition["field_specs"]:
        field_name = spec["field"]
        statuses = sorted({
            item["status"]
            for audit in audits
            for item in audit["fields"]
            if item["field"] == field_name
        })
        max_items = max(
            item["item_count"]
            for audit in audits
            for item in audit["fields"]
            if item["field"] == field_name
        )
        fields.append({
            "field": field_name,
            "kind": spec["kind"],
            "statuses": statuses,
            "max_item_count": max_items,
        })
    union_footprint = sorted({
        tuple(row["wr_key"])
        for audit in audits
        for row in audit["wr_key_footprint"]
    })
    covered_anywhere = {
        tuple(row["wr_key"])
        for audit in audits
        for row in audit["wr_key_footprint"]
        if row["legacy_equivalent"]
    }
    union_wr_only = sorted(
        key for key in union_footprint if key not in covered_anywhere
    )
    gaps = definition.get("gaps", [])
    recommendation = _recommend(
        consumer_id, fields, union_wr_only, gaps
    )
    return {
        "consumer": consumer_id,
        "projection": definition["projection"],
        "fields": fields,
        "wr_key_footprint_count": len(union_footprint),
        "wr_only_key_count": len(union_wr_only),
        "wr_only_keys": [list(key) for key in union_wr_only],
        "gaps": gaps,
        "recommendation": recommendation,
    }


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "reports" / "wr310-consumer-read-switch-audit-2026-08-07.json"


def main() -> None:
    from app.writing.world_runtime_checkpoint_shadow import (
        build_shadow_payload,
        merge_shadow,
        verify_shadow_payload,
    )
    from app.writing.world_runtime_character_projection import project_characters
    from app.writing.world_runtime_handover_projection import project_handover
    from app.writing.world_runtime_metadata_projection import (
        project_rag_metadata,
        project_world_state_facts,
    )
    from app.writing.world_runtime_reviewer_projection import (
        project_reviewer_context,
    )

    per_consumer: dict[str, list[dict[str, Any]]] = {
        consumer_id: [] for consumer_id in CONSUMER_DEFS
    }
    subsections = []
    for subsection in range(1, 4):
        legacy_facts = _legacy_frame_facts(subsection)
        wr_facts = _wr_frame_facts(subsection)
        committed = CommittedWorldState.model_validate(
            json.loads(
                (
                    ROOT
                    / ".world_runtime_state_commit_canary_runtime/c21r10/private/commits"
                    / f"S{subsection}.json"
                ).read_text(encoding="utf-8")
            )
        )
        key_result = key_level_compare(legacy_facts, wr_facts)
        projections = {
            "handover": project_handover(committed),
            "world_state": project_world_state_facts(committed),
            "character_store": project_characters(committed),
            "rag_metadata": project_rag_metadata(committed),
            "reviewer": project_reviewer_context(committed),
        }
        shadow_payload = build_shadow_payload(committed)
        shadow_verified, shadow_issues = verify_shadow_payload(shadow_payload)
        sample_checkpoint = {"legacy_key": "kept"}
        merged_checkpoint = merge_shadow(sample_checkpoint, shadow_payload)
        projections["checkpoint"] = {
            "shadow_payload_present": merged_checkpoint.get("world_runtime_shadow_v1") is not None,
            "legacy_keys_preserved": sample_checkpoint == {"legacy_key": "kept"},
            "shadow_verified": shadow_verified,
            "shadow_issues": shadow_issues,
        }
        subsection_audits = {}
        for consumer_id, projection in projections.items():
            audit = audit_consumer(consumer_id, projection, key_result)
            per_consumer[consumer_id].append(audit)
            subsection_audits[consumer_id] = audit
        subsections.append({
            "subsection": subsection,
            "consumers": subsection_audits,
        })
    consumers = [
        aggregate_consumer(consumer_id, audits)
        for consumer_id, audits in per_consumer.items()
    ]
    report = {
        "schema_version": "wr310-consumer-read-switch-audit-v1",
        "note": (
            "offline coverage audit over frozen C2.1-R10 commits; "
            "key-level matrix wr39-key-level-semantic-v2"
        ),
        "consumers": consumers,
        "subsections": subsections,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "schema_version": report["schema_version"],
                "output": str(OUTPUT),
                "consumers": [
                    {
                        "consumer": item["consumer"],
                        "recommendation": item["recommendation"],
                        "wr_key_footprint_count": item["wr_key_footprint_count"],
                        "wr_only_key_count": item["wr_only_key_count"],
                        "gaps": item["gaps"],
                    }
                    for item in consumers
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
