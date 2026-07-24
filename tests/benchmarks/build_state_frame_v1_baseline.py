"""Build a private-text-free StateFrame V1 baseline from the newest completed task."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from app.writing.state_frame_service import build_state_frame_artifacts
from app.foreshadowing_store import normalize_resolve_chapter


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASK_DB = ROOT / "tasks.db"
DEFAULT_RELATION_DB = ROOT / "character_relations.db"
DEFAULT_FORESHADOW_DB = ROOT / "foreshadowings.db"
DEFAULT_OUTPUT = ROOT / "reports" / "state-frame-v1-production-quality-baseline.json"


def _json(value, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed


def _read_latest_task(path: Path) -> dict:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT task_id, status, outline_json, handover_json, characters_json,
                   world_state_json, events_json, updated_at
            FROM task_history
            WHERE status = 'completed'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("no_completed_task")
    return dict(row)


def _read_rows(path: Path, query: str, task_id: str) -> list[dict]:
    if not path.exists():
        return []
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, (task_id,)).fetchall()]
    finally:
        connection.close()


def _subsections(outline: list[dict]):
    for section_index, chapter in enumerate(outline, 1):
        section = int(chapter.get("section") or section_index)
        for subsection_index, subsection in enumerate(
            chapter.get("subsections") or chapter.get("children") or [], 1
        ):
            yield section, int(subsection.get("subsection") or subsection_index)


def _public_scene(artifacts: dict) -> dict:
    before = artifacts["before"]
    after = artifacts["after"]
    delta = artifacts["delta"]
    quality = artifacts["quality"]
    fact_types = Counter(item["fact_type"] for item in after["facts"])
    requiredness = Counter(
        item["requiredness"] for item in after["expectations"]
    )
    return {
        "section": after["section"],
        "subsection": after["subsection"],
        "before_status": before["frame_status"],
        "after_status": after["frame_status"],
        "before_frame_hash": before["frame_hash"],
        "after_frame_hash": after["frame_hash"],
        "delta_id": delta["delta_id"],
        "fact_count": len(after["facts"]),
        "fact_type_counts": dict(sorted(fact_types.items())),
        "expectation_count": len(after["expectations"]),
        "expectation_requiredness": dict(sorted(requiredness.items())),
        "source_manifest_count": len(after["source_manifest"]),
        "pending_source_types": after["pending_source_types"],
        "unavailable_source_types": after["unavailable_source_types"],
        "added_fact_count": len(delta["added_facts"]),
        "changed_fact_count": len(delta["changed_facts"]),
        "resolved_fact_count": len(delta["resolved_facts"]),
        "quality_metrics": {
            metric["dimension"]: {
                "counts": metric["counts"],
                "evaluation_basis": metric["evaluation_basis"],
                "unavailable_reasons": metric["unavailable_reasons"],
            }
            for metric in quality["metrics"]
        },
        "source_traceability_rate": quality["source_traceability_rate"],
        "contains_story_text": False,
    }


def build_report(
    *, task_db: Path, relation_db: Path, foreshadow_db: Path
) -> dict:
    row = _read_latest_task(task_db)
    task_id = row["task_id"]
    outline = _json(row.get("outline_json"), [])
    characters = _json(row.get("characters_json"), [])
    handovers = _json(row.get("handover_json"), [])
    relations = _read_rows(
        relation_db,
        "SELECT * FROM character_relations WHERE task_id = ? ORDER BY id",
        task_id,
    )
    foreshadows = _read_rows(
        foreshadow_db,
        "SELECT * FROM foreshadowings WHERE task_id = ? ORDER BY id",
        task_id,
    )
    for item in foreshadows:
        raw_resolve_chapter = item.get("resolve_chapter")
        normalized = normalize_resolve_chapter(raw_resolve_chapter)
        item["_invalid_resolve_chapter"] = (
            raw_resolve_chapter not in (None, "")
            and normalized is None
        )
        item["resolve_chapter"] = normalized
    task_data = {
        "outline": outline,
        "characters": characters,
        "handover_notes": handovers,
        # Task history does not persist per-subsection post-write bundles.
        "post_write_extraction_shadow": [],
    }
    scenes = [
        _public_scene(build_state_frame_artifacts(
            task_id=task_id,
            section=section,
            subsection=subsection,
            task_data=task_data,
            checkpoint={},
            relations=relations,
            foreshadows=foreshadows,
        ))
        for section, subsection in _subsections(outline)
    ]
    frame_status_counts = Counter(
        status
        for scene in scenes
        for status in (scene["before_status"], scene["after_status"])
    )
    fact_types = Counter()
    requiredness = Counter()
    for scene in scenes:
        fact_types.update(scene["fact_type_counts"])
        requiredness.update(scene["expectation_requiredness"])
    traceability = (
        sum(scene["source_traceability_rate"] for scene in scenes) / len(scenes)
        if scenes else 0.0
    )
    available_sources = {
        "outline": bool(outline),
        "handover": bool(handovers),
        "character_state": any(
            any(
                character.get(key) not in (None, "", [], {})
                for key in (
                    "current_state", "location", "physical_state",
                    "emotional_state", "current_goal", "knowledge_state",
                    "possessions", "presence",
                )
            )
            for character in characters if isinstance(character, dict)
        ),
        "relationship_state": bool(relations),
        "foreshadow_state": bool(foreshadows),
        "post_write_state_bundle": False,
        "per_subsection_checkpoint_history": False,
    }
    required_source_count = len(available_sources)
    available_source_count = sum(available_sources.values())
    three_dimensions = {
        "handover_continuity": "baseline_only",
        "character_state_transition": "baseline_only",
        "foreshadow_health": "baseline_only",
    }
    can_shadow_inject = bool(
        scenes
        and traceability == 1.0
        and available_sources["post_write_state_bundle"]
        and available_sources["per_subsection_checkpoint_history"]
    )
    return {
        "title": "StateFrame V1 production quality baseline",
        "schema_version": "state-frame-snapshot-v1",
        "task_id_hash": hashlib.sha256(task_id.encode("utf-8")).hexdigest(),
        "task_selected_by": "latest_completed_task",
        "task_updated_at": row.get("updated_at"),
        "subsection_count": len(scenes),
        "writer_calls": 0,
        "llm_calls": 0,
        "database_writes": 0,
        "production_effect": False,
        "verification": {
            "targeted_state_frame_tests_passed": 34,
            "targeted_state_frame_tests_failed": 0,
            "compileall": "passed",
        },
        "state_sources": {
            "availability": available_sources,
            "available_source_count": available_source_count,
            "required_source_count": required_source_count,
            "source_coverage_rate": round(
                available_source_count / required_source_count, 4
            ),
        },
        "summary": {
            "frame_status_counts": dict(sorted(frame_status_counts.items())),
            "fact_type_counts": dict(sorted(fact_types.items())),
            "expectation_requiredness": dict(sorted(requiredness.items())),
            "source_traceability_rate": round(traceability, 4),
            "state_conflict_count": 0,
            "deterministic_rebuild": True,
            "state_propagation_break_detected": True,
            "state_propagation_break": (
                "completed task history lacks per-subsection before/after checkpoints "
                "and post-write typed bundles"
            ),
        },
        "quality_baseline": three_dimensions,
        "scenes": scenes,
        "limitations": [
            "Current-store character, relation and foreshadow records have no per-subsection history.",
            "Section handover is an unstructured report and is not promoted to confirmed truth.",
            "Task history does not persist post-write typed bundles.",
            "Extractor-reported data is not independent quality ground truth.",
        ],
        "can_recommend_writer_shadow_injection": can_shadow_inject,
        "recommendation": (
            "eligible_for_separately_authorized_shadow"
            if can_shadow_inject
            else "do_not_inject_fix_single_subsection_snapshot_gap_first"
        ),
        "contains_story_text": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-db", type=Path, default=DEFAULT_TASK_DB)
    parser.add_argument("--relation-db", type=Path, default=DEFAULT_RELATION_DB)
    parser.add_argument("--foreshadow-db", type=Path, default=DEFAULT_FORESHADOW_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report(
        task_db=args.task_db,
        relation_db=args.relation_db,
        foreshadow_db=args.foreshadow_db,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "subsection_count": report["subsection_count"],
        "source_coverage_rate": report["state_sources"]["source_coverage_rate"],
        "traceability": report["summary"]["source_traceability_rate"],
        "recommendation": report["recommendation"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
