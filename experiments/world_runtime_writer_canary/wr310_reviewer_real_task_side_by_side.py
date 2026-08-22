"""WR3.10+ Reviewer real-task snapshot + side-by-side (offline, read-only).

Completes the Reviewer context side-by-side for the three fields whose legacy
inputs were not part of the frozen WR3.9 snapshot:

- character_consistency_context: legacy characters_json (task_history) rendered
  with CharacterFormatter.build_context; character_arcs live in the Redis
  blackboard and are recorded as unavailable offline;
- relation_context: legacy character_relations rows rendered with the same
  format as build_relation_context;
- subplot_context: legacy subplots rows (DB is empty in the current workspace).

The WR side uses the frozen C2.1-R10 commit.  The legacy task is a different
run of the same story universe, so every comparison is explicitly labelled
cross-task semantic reference, not identical-text equivalence.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from app.agents.character_formatter import CharacterFormatter
from app.character_relation_store import list_relations_read_only
from app.writing.world_runtime_reviewer_projection import (
    project_reviewer_context,
)
from app.writing.world_runtime_state_committer import CommittedWorldState


REAL_TASK_ID = "cd830826-61b0-4840-b2a7-45cf807599e0"

ROOT = Path(__file__).resolve().parents[2]
TASKS_DB = ROOT / "tasks.db"
SUBPLOTS_DB = ROOT / "subplots.db"
CANARY_COMMITS = ROOT / ".world_runtime_state_commit_canary_runtime/c21r10/private/commits"
OUTPUT = ROOT / "reports" / "wr310-reviewer-real-task-side-by-side-2026-08-07.json"

DIRECTION_LABELS = {"positive": "正向", "negative": "负向", "complex": "复杂"}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_relation_context(relations: list[dict[str, Any]]) -> str:
    """Render the same legacy relation context format as build_relation_context."""
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
        direction = DIRECTION_LABELS.get(
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


def _read_task_history(task_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(
        f"file:{TASKS_DB.resolve().as_posix()}?mode=ro", uri=True
    )
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM task_history WHERE task_id = ? ORDER BY rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"task not found: {task_id}")
    return dict(row)


def _read_subplots(task_id: str) -> list[dict[str, Any]]:
    if not SUBPLOTS_DB.exists():
        return []
    conn = sqlite3.connect(
        f"file:{SUBPLOTS_DB.resolve().as_posix()}?mode=ro", uri=True
    )
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM subplots WHERE task_id = ?", (task_id,)
            ).fetchall()
        ]
    finally:
        conn.close()


def collect_real_task_snapshot(task_id: str = REAL_TASK_ID) -> dict[str, Any]:
    """Collect the legacy Reviewer inputs for one real task, read-only."""
    history = _read_task_history(task_id)
    characters = json.loads(history.get("characters_json") or "[]")
    handover = json.loads(history.get("handover_json") or "[]")
    relations = list_relations_read_only(task_id)
    subplots = _read_subplots(task_id)
    legacy_character_context = CharacterFormatter.build_context(characters)
    legacy_relation_context = render_relation_context(relations)
    legacy_subplot_context = ""
    return {
        "task_id": task_id,
        "topic": history.get("topic"),
        "status": history.get("status"),
        "created_at": history.get("created_at"),
        "characters": characters,
        "character_count": len(characters),
        "legacy_character_context": legacy_character_context,
        "character_arcs_status": "unavailable_redis_blackboard",
        "handover_note_count": len(handover),
        "relations": relations,
        "relation_count": len(relations),
        "legacy_relation_context": legacy_relation_context,
        "subplots": subplots,
        "subplot_count": len(subplots),
        "legacy_subplot_context": legacy_subplot_context,
        "source_hashes": {
            "characters_json": _sha256_text(json.dumps(characters, ensure_ascii=False, sort_keys=True)),
            "handover_json": _sha256_text(json.dumps(handover, ensure_ascii=False, sort_keys=True)),
            "relation_rows": _sha256_text(json.dumps(relations, ensure_ascii=False, sort_keys=True, default=str)),
        },
    }


def compare_character_context(
    snapshot: dict[str, Any], wr_reviewer: dict[str, Any]
) -> dict[str, Any]:
    return {
        "field": "character_consistency_context",
        "legacy_status": "real_task_snapshot",
        "legacy_character_count": snapshot["character_count"],
        "legacy_text_length": len(snapshot["legacy_character_context"]),
        "legacy_arcs_status": snapshot["character_arcs_status"],
        "wr_status": "projected_from_wr",
        "wr_text_length": len(str(wr_reviewer.get("character_consistency_context", ""))),
        "value_status": "different_shape",
    }


def compare_relation_context(
    snapshot: dict[str, Any], wr_reviewer: dict[str, Any]
) -> dict[str, Any]:
    return {
        "field": "relation_context",
        "legacy_status": "real_task_snapshot",
        "legacy_relation_count": snapshot["relation_count"],
        "legacy_text_length": len(snapshot["legacy_relation_context"]),
        "wr_status": "legacy_only_placeholder",
        "wr_text": str(wr_reviewer.get("relation_context", "")),
        "data_loss_risk": snapshot["relation_count"] > 0,
    }


def compare_subplot_context(
    snapshot: dict[str, Any], wr_reviewer: dict[str, Any]
) -> dict[str, Any]:
    return {
        "field": "subplot_context",
        "legacy_status": "real_task_snapshot",
        "legacy_subplot_count": snapshot["subplot_count"],
        "legacy_text_length": len(snapshot["legacy_subplot_context"]),
        "wr_status": "legacy_only_placeholder",
        "wr_text": str(wr_reviewer.get("subplot_context", "")),
        "data_loss_risk": snapshot["subplot_count"] > 0,
    }


def compare_handover_reference(
    snapshot: dict[str, Any], wr_reviewer: dict[str, Any]
) -> dict[str, Any]:
    return {
        "field": "handover_chain",
        "legacy_status": "real_task_snapshot",
        "legacy_note_count": snapshot["handover_note_count"],
        "wr_status": "projected_from_wr",
        "note": (
            "cross-task reference: real task handover notes vs C2.1-R10 WR "
            "commit; same-task WR commit does not exist"
        ),
    }


def build_real_task_report(
    snapshot: dict[str, Any],
    committed: CommittedWorldState,
) -> dict[str, Any]:
    wr_reviewer = project_reviewer_context(committed)
    fields = [
        compare_handover_reference(snapshot, wr_reviewer),
        compare_character_context(snapshot, wr_reviewer),
        compare_relation_context(snapshot, wr_reviewer),
        compare_subplot_context(snapshot, wr_reviewer),
    ]
    data_loss_fields = [
        item["field"] for item in fields if item.get("data_loss_risk")
    ]
    return {
        "fields": fields,
        "summary": {
            "field_count": len(fields),
            "data_loss_risk_fields": data_loss_fields,
            "legacy_sources_unavailable": [
                "character_arcs (Redis blackboard)"
            ],
        },
        "recommendation": (
            "blocked_until_relationship_ontology_or_legacy_retention"
            if data_loss_fields
            else "needs_character_rich_field_decision"
        ),
    }


def _load_committed(subsection: int) -> CommittedWorldState:
    return CommittedWorldState.model_validate(
        json.loads(
            (CANARY_COMMITS / f"S{subsection}.json").read_text(encoding="utf-8")
        )
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    snapshot = collect_real_task_snapshot()
    report = build_real_task_report(snapshot, _load_committed(1))
    payload = {
        "schema_version": "wr310-reviewer-real-task-side-by-side-v1",
        "note": (
            "legacy side from real task cd830826 (read-only snapshot), WR side "
            "from frozen C2.1-R10 S1 commit; cross-task semantic reference"
        ),
        "report": report,
        "snapshot": snapshot,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
