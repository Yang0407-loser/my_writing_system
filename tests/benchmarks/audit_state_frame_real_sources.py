"""Audit real frozen StateFrame sources without reading generated candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from app.writing.scene_compiler import SceneCompiler
from app.writing.state_frame import StateFrameCompiler
from app.writing.story_state_view import StoryStateView


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / ".phase4r_final_trial_runtime" / "source.private.json"
DEFAULT_RUNTIME = ROOT / ".phase4r_state_frame_runtime"
DEFAULT_OUTPUT = ROOT / "reports" / "state-frame-batch2-real-source-coverage.json"

SOURCE_TYPES = (
    "current_outline", "world_state", "event_graph", "character_arcs",
    "handover", "rules", "relations", "foreshadowing", "locations",
    "characters",
)
CLASSIFICATIONS = (
    "explicit_structured_state", "generic_state", "unclassified_state",
    "unavailable_state",
)
STATUS_VALUES = ("confirmed", "planned", "unknown", "conflicted")
EXPLICIT_PREDICATES = {
    predicate
    for predicates in StateFrameCompiler.CATEGORY_PREDICATES.values()
    for predicate in predicates
} | {"open_loop"}
GENERIC_PREDICATES = {"world_fact", "continuity_state"}


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_hash(value: Any) -> str:
    return _hash_text(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _estimate_tokens(text: str) -> int:
    chinese = sum("\u4e00" <= char <= "\u9fff" for char in text)
    return max(1, int(chinese * 1.5 + (len(text) - chinese) * 0.3))


def _empty_metrics(*, available: bool, shape: str, raw_count: int) -> dict[str, Any]:
    return {
        "available": available,
        "shape": shape,
        "raw_record_count": raw_count,
        "eligible_record_count": 0,
        "evidence_count": 0,
        "assertion_count": 0,
        "statuses": {status: 0 for status in STATUS_VALUES},
        "classifications": {name: 0 for name in CLASSIFICATIONS},
        "predicates": {},
        "generic_world_fact_count": 0,
        "excluded_count": 0,
        "missing_stable_source_id": 0,
        "missing_section_subsection": 0,
        "missing_epistemic_status": 0,
        "unstructured_handover_entries": 0,
        "empty_value_count": 0,
        "duplicate_source_id_count": 0,
        "duplicate_value_hash_count": 0,
        "skip_reasons": [],
    }


def _classify(predicate: str) -> str:
    if predicate in GENERIC_PREDICATES:
        return "generic_state"
    if predicate in EXPLICIT_PREDICATES:
        return "explicit_structured_state"
    return "unclassified_state"


class _SceneAuditBuilder:
    def __init__(self, *, section: int, subsection: int, source_shapes: dict[str, dict]) -> None:
        self.section = section
        self.subsection = subsection
        self.sources: list[dict[str, Any]] = []
        self.assertions: list[dict[str, Any]] = []
        self.assertion_ledger: list[dict[str, Any]] = []
        self.metrics = {
            name: _empty_metrics(**source_shapes[name]) for name in SOURCE_TYPES
        }
        self._source_ids: set[str] = set()
        self._value_hashes: set[str] = set()

    def add(
        self,
        *,
        source_type: str,
        source_id: str,
        value: Any,
        predicate: str,
        status: str,
        source_section: int | None,
        source_subsection: int | None,
        source_id_was_derived: bool = False,
        epistemic_status_was_derived: bool = False,
        unstructured_handover: bool = False,
    ) -> None:
        metric = self.metrics[source_type]
        text = _text(value)
        if not text:
            metric["empty_value_count"] += 1
            metric["skip_reasons"].append("empty_value")
            return
        metric["eligible_record_count"] += 1
        if source_id_was_derived:
            metric["missing_stable_source_id"] += 1
        if source_section in (None, 0) or source_subsection in (None, 0):
            metric["missing_section_subsection"] += 1
        if epistemic_status_was_derived:
            metric["missing_epistemic_status"] += 1
        if unstructured_handover:
            metric["unstructured_handover_entries"] += 1
        if source_id in self._source_ids:
            metric["duplicate_source_id_count"] += 1
            metric["skip_reasons"].append("duplicate_source_id")
            return
        value_hash = _hash_text(text)
        if value_hash in self._value_hashes:
            metric["duplicate_value_hash_count"] += 1
            metric["skip_reasons"].append("duplicate_value_hash")
            return
        self._source_ids.add(source_id)
        self._value_hashes.add(value_hash)
        evidence_id = f"ev:{source_type}:{source_id}"
        assertion_id = f"state:{source_type}:{source_id}"
        classification = _classify(predicate)
        self.sources.append({
            "evidence_id": evidence_id,
            "source_id": source_id,
            "source_type": source_type,
            "text": text,
            "section": source_section,
            "subsection": source_subsection,
            "span_start": 0,
            "span_end": len(text),
        })
        self.assertions.append({
            "assertion_id": assertion_id,
            "subject": source_type,
            "predicate": predicate,
            "value": text,
            "status": status,
            "evidence_ids": [evidence_id],
        })
        self.assertion_ledger.append({
            "assertion_id": assertion_id,
            "source_id": source_id,
            "source_type": source_type,
            "predicate": predicate,
            "status": status,
            "classification": classification,
            "value_hash": value_hash,
            "characters": len(text),
        })
        metric["evidence_count"] += 1
        metric["assertion_count"] += 1
        metric["statuses"][status] += 1
        metric["classifications"][classification] += 1
        metric["predicates"][predicate] = metric["predicates"].get(predicate, 0) + 1
        if predicate == "world_fact":
            metric["generic_world_fact_count"] += 1

    def finish_unavailable(self) -> None:
        for source_type, metric in self.metrics.items():
            if metric["assertion_count"] == 0 and source_type not in {"rules", "current_outline"}:
                metric["classifications"]["unavailable_state"] = 1
                if not metric["skip_reasons"]:
                    metric["skip_reasons"].append(
                        "source_empty" if metric["raw_record_count"] == 0 else "no_current_state_contract"
                    )
            metric["skip_reasons"] = sorted(set(metric["skip_reasons"]))
            metric["predicates"] = dict(sorted(metric["predicates"].items()))


def _source_shapes(source: dict[str, Any]) -> dict[str, dict]:
    task = source.get("task", {})
    checkpoint = source.get("checkpoint", {})
    frozen = source.get("frozen_contexts", {})
    world = task.get("world_state") or {}
    handovers = checkpoint.get("_prev_handover") or []
    return {
        "current_outline": {"available": bool(source.get("outline")), "shape": "structured_outline", "raw_count": 1},
        "world_state": {"available": bool(world), "shape": "facts_with_verified_flag", "raw_count": len(world.get("facts") or []) + len(world.get("contradictions") or [])},
        "event_graph": {"available": bool(task.get("event_graph")), "shape": "typed_events", "raw_count": len(task.get("event_graph") or [])},
        "character_arcs": {"available": bool(checkpoint.get("character_arcs")), "shape": "structured_arc_with_string_current_state", "raw_count": len(checkpoint.get("character_arcs") or [])},
        "handover": {"available": bool(handovers), "shape": "structured_envelope_with_unstructured_strings", "raw_count": len(handovers)},
        "rules": {"available": bool(str(frozen.get("rules", "")).strip()), "shape": "rendered_context_string", "raw_count": int(bool(str(frozen.get("rules", "")).strip()))},
        "relations": {"available": bool(str(frozen.get("relations", "")).strip()), "shape": "rendered_context_string", "raw_count": int(bool(str(frozen.get("relations", "")).strip()))},
        "foreshadowing": {"available": bool(str(frozen.get("foreshadowing", "")).strip()), "shape": "rendered_context_string", "raw_count": int(bool(str(frozen.get("foreshadowing", "")).strip()))},
        "locations": {"available": bool(str(frozen.get("locations", "")).strip()), "shape": "rendered_context_string", "raw_count": int(bool(str(frozen.get("locations", "")).strip()))},
        "characters": {"available": bool(checkpoint.get("characters")), "shape": "character_profiles_without_effective_state", "raw_count": len(checkpoint.get("characters") or [])},
    }


def _build_scene(source: dict[str, Any], subsection: dict[str, Any]) -> tuple[dict, dict]:
    task = source.get("task", {})
    checkpoint = source.get("checkpoint", {})
    frozen = source.get("frozen_contexts", {})
    section = int(source["outline"][0].get("section", 1))
    subsection_number = int(subsection.get("subsection", 0))
    builder = _SceneAuditBuilder(
        section=section,
        subsection=subsection_number,
        source_shapes=_source_shapes(source),
    )

    outline_source_id = str(subsection.get("source_id") or f"outline:S{section}.{subsection_number}")
    planned_values = subsection.get("key_points") or [subsection.get("title", "")]
    for index, value in enumerate(planned_values, 1):
        builder.add(
            source_type="current_outline",
            source_id=f"{outline_source_id}:{index}",
            value=value,
            predicate="planned_event",
            status="planned",
            source_section=section,
            source_subsection=subsection_number,
            source_id_was_derived="source_id" not in subsection,
            epistemic_status_was_derived=True,
        )

    world = task.get("world_state") or {}
    for index, fact in enumerate(world.get("facts") or [], 1):
        builder.add(
            source_type="world_state",
            source_id=str(fact.get("fact_id") or f"world:{index}"),
            value=fact.get("fact", ""),
            predicate="world_fact",
            status="confirmed" if fact.get("verified") is True else "unknown",
            source_section=fact.get("source_section"),
            source_subsection=fact.get("source_subsection"),
            source_id_was_derived=not bool(fact.get("fact_id")),
            epistemic_status_was_derived=True,
        )
    for index, conflict in enumerate(world.get("contradictions") or [], 1):
        builder.add(
            source_type="world_state",
            source_id=str(conflict.get("new_fact_id") or f"world-conflict:{index}"),
            value=conflict,
            predicate="world_fact",
            status="conflicted",
            source_section=conflict.get("source_section"),
            source_subsection=conflict.get("source_subsection"),
            source_id_was_derived=not bool(conflict.get("new_fact_id")),
            epistemic_status_was_derived=True,
        )

    for index, event in enumerate(task.get("event_graph") or [], 1):
        if int(event.get("section", 0)) != section or int(event.get("subsection", 0)) != subsection_number:
            continue
        status = "confirmed" if event.get("status") == "established" else "planned"
        builder.add(
            source_type="event_graph",
            source_id=str(event.get("event_id") or f"event:{index}"),
            value=event.get("description", ""),
            predicate="arc_milestone",
            status=status,
            source_section=event.get("section"),
            source_subsection=event.get("subsection"),
            source_id_was_derived=not bool(event.get("event_id")),
            epistemic_status_was_derived=False,
        )

    for index, arc in enumerate(checkpoint.get("character_arcs") or [], 1):
        builder.add(
            source_type="character_arcs",
            source_id=f"character-arc:{arc.get('character_id') or index}",
            value=arc.get("current_state", ""),
            predicate="character_state",
            status="confirmed",
            source_section=None,
            source_subsection=None,
            source_id_was_derived=True,
            epistemic_status_was_derived=True,
        )

    for handover_index, handover in enumerate(checkpoint.get("_prev_handover") or [], 1):
        from_section = handover.get("from_section")
        to_section = handover.get("to_section")
        fields = (
            ("character_state", "continuity_state", "confirmed"),
            ("open_threads", "open_loop", "planned"),
            ("foreshadowing", "open_loop", "planned"),
        )
        for field, predicate, status in fields:
            builder.add(
                source_type="handover",
                source_id=f"handover:{from_section}:{to_section}:{handover_index}:{field}",
                value=handover.get(field, ""),
                predicate=predicate,
                status=status,
                source_section=from_section,
                source_subsection=None,
                source_id_was_derived=True,
                epistemic_status_was_derived=True,
                unstructured_handover=True,
            )

    rules = str(frozen.get("rules", "")).strip()
    if rules:
        builder.add(
            source_type="rules",
            source_id="frozen-context:rules",
            value=rules,
            predicate="hard_constraint",
            status="confirmed",
            source_section=section,
            source_subsection=subsection_number,
            source_id_was_derived=True,
            epistemic_status_was_derived=True,
        )

    builder.finish_unavailable()
    snapshot = StoryStateView(
        task_id=str(source.get("task_id", "frozen-task")),
        section=section,
        subsection=subsection_number,
    ).project(builder.sources, builder.assertions)
    compiler = StateFrameCompiler()
    frame = compiler.compile(snapshot)
    repeated = compiler.compile(snapshot)
    rendered = compiler.render(frame)
    scene_spec = SceneCompiler().compile(snapshot)
    scene_rendered = SceneCompiler().render(scene_spec)

    frame_groups = (
        frame.temporal_state, frame.location_state, frame.character_presence,
        frame.persistent_state, frame.relationship_state, frame.open_loops,
        frame.unknowns_and_conflicts,
    )
    included = [item for group in frame_groups for item in group]
    included_ids = [item.assertion_id for item in included]
    referenced_evidence = {
        evidence_id for item in included for evidence_id in item.evidence_ids
    }
    frame_evidence_ids = {item.evidence_id for item in frame.evidence}
    parent_by_id = {item.assertion_id: item for item in snapshot.assertions}
    status_preserved = all(
        parent_by_id[item.assertion_id].status == item.status for item in included
    )
    planned_hard_intrusions = sum(
        item.predicate in {"planned_event", "hard_constraint", "arc_milestone"}
        for item in included
    )

    scene_confirmed_open = scene_spec.confirmed_state + scene_spec.open_loops
    scene_ids = {item.assertion_id for item in scene_confirmed_open}
    assertion_overlap = len(set(included_ids) & scene_ids)
    value_hashes = Counter(_hash_text(item.value.strip()) for item in included)
    unique_state_count = len(value_hashes)
    duplicate_value_count = sum(count - 1 for count in value_hashes.values() if count > 1)
    world_source_ids = {
        item["source_id"] for item in builder.assertion_ledger
        if item["source_type"] == "world_state"
    }
    handover_source_ids = {
        item["source_id"] for item in builder.assertion_ledger
        if item["source_type"] == "handover"
    }
    included_source_ids = {
        source.source_id for source in frame.evidence
    }
    scene_core = scene_spec.model_dump()
    scene_core["confirmed_state"] = []
    scene_core["open_loops"] = []
    plan_only_rendered = SceneCompiler.render_fields(scene_core)

    classification_counts = Counter(item["classification"] for item in builder.assertion_ledger)
    public_scene = {
        "trial_index": subsection_number,
        "section": section,
        "subsection": subsection_number,
        "frame_hash": frame.frame_hash,
        "source_hash": frame.source_hash,
        "rendered_hash": _hash_text(rendered),
        "frame_hash_deterministic": frame.frame_hash == repeated.frame_hash,
        "rendered_characters": len(rendered),
        "estimated_tokens": frame.estimated_tokens,
        "counts": {
            "temporal_state": len(frame.temporal_state),
            "location_state": len(frame.location_state),
            "character_presence": len(frame.character_presence),
            "persistent_state": len(frame.persistent_state),
            "relationship_state": len(frame.relationship_state),
            "open_loops": len(frame.open_loops),
            "unknowns_conflicts": len(frame.unknowns_and_conflicts),
            "excluded": len(frame.excluded_assertion_ids),
        },
        "classification_counts": dict(sorted(classification_counts.items())),
        "empty_frame": len(included) == 0,
        "generic_only_frame": bool(included) and classification_counts["explicit_structured_state"] == 0,
        "status_preserved": status_preserved,
        "traceability_rate": 1.0 if referenced_evidence == frame_evidence_ids else 0.0,
        "unknown_conflicted_preserved": sum(
            item.status in {"unknown", "conflicted"} for item in frame.unknowns_and_conflicts
        ),
        "planned_hard_intrusions": planned_hard_intrusions,
        "duplicate_classification_count": len(included_ids) - len(set(included_ids)),
        "duplicate_value_hash_count": duplicate_value_count,
        "unreferenced_frame_evidence_count": len(frame_evidence_ids - referenced_evidence),
        "missing_evidence_reference_count": len(referenced_evidence - frame_evidence_ids),
        "source_metrics": builder.metrics,
        "duplication": {
            "stateframe_vs_scene_confirmed_open_assertions": assertion_overlap,
            "stateframe_vs_legacy_world_source_ids": len(included_source_ids & world_source_ids),
            "stateframe_vs_handover_source_ids": len(included_source_ids & handover_source_ids),
            "stateframe_vs_relation_context": 0,
            "unique_state_count": unique_state_count,
            "direct_stack_added_tokens": frame.estimated_tokens,
            "scene_spec_tokens": scene_spec.estimated_tokens,
            "stateframe_plus_plan_only_scene_tokens": frame.estimated_tokens + _estimate_tokens(plan_only_rendered),
            "confirmed_open_takeover_net_tokens": (
                frame.estimated_tokens + _estimate_tokens(plan_only_rendered) - scene_spec.estimated_tokens
            ),
        },
        "contains_story_text": False,
    }
    private_scene = {
        **public_scene,
        "assertion_ledger": builder.assertion_ledger,
        "snapshot_assertion_ids": [item.assertion_id for item in snapshot.assertions],
        "frame_assertion_ids": included_ids,
        "scene_spec_rendered_hash": _hash_text(scene_rendered),
    }
    return public_scene, private_scene


def build_audit(source: dict[str, Any], source_snapshot_sha256: str) -> tuple[dict, dict]:
    outline = source.get("outline") or []
    if len(outline) != 1 or len(outline[0].get("subsections") or []) != 4:
        raise ValueError("real-source audit requires exactly four frozen subsections")
    public_scenes = []
    private_scenes = []
    for subsection in outline[0]["subsections"]:
        public_scene, private_scene = _build_scene(source, subsection)
        public_scenes.append(public_scene)
        private_scenes.append(private_scene)

    tokens = [item["estimated_tokens"] for item in public_scenes]
    all_ledgers = [item for scene in private_scenes for item in scene["assertion_ledger"]]
    classification_counts = Counter(item["classification"] for item in all_ledgers)
    included_classifications = (
        classification_counts["explicit_structured_state"]
        + classification_counts["generic_state"]
    )
    generic_ratio = (
        classification_counts["generic_state"] / included_classifications
        if included_classifications else 0.0
    )
    unknown_parent = sum(
        item["status"] in {"unknown", "conflicted"} for item in all_ledgers
    )
    unknown_frame = sum(item["unknown_conflicted_preserved"] for item in public_scenes)
    mechanical = {
        "all_sources_traceable": all(item["traceability_rate"] == 1.0 for item in public_scenes),
        "unknown_conflicted_retention_100": unknown_parent == unknown_frame,
        "planned_hard_intrusions_zero": all(item["planned_hard_intrusions"] == 0 for item in public_scenes),
        "duplicate_classification_zero": all(item["duplicate_classification_count"] == 0 for item in public_scenes),
        "frame_hash_deterministic": all(item["frame_hash_deterministic"] for item in public_scenes),
        "three_of_four_have_explicit_state": sum(
            item["classification_counts"].get("explicit_structured_state", 0) > 0
            for item in public_scenes
        ) >= 3,
        "generic_state_not_majority": generic_ratio <= 0.5,
        "keyword_inference_not_used": True,
    }
    if included_classifications == 0:
        diagnosis = "insufficient_real_source_data"
    elif all(mechanical.values()):
        diagnosis = "ready_for_composition_contract"
    else:
        diagnosis = "upstream_state_contract_required"

    source_availability = _source_shapes(source)
    public = {
        "phase": "StateFrame Batch 2",
        "mode": "real_frozen_source_coverage_audit",
        "schema_version": "state-frame-real-source-audit-v1",
        "source_snapshot_sha256": source_snapshot_sha256,
        "scene_count": 4,
        "writer_generation_calls": 0,
        "llm_calls": 0,
        "redis_writes": 0,
        "sqlite_writes": 0,
        "chroma_writes": 0,
        "runtime_evaluation_fields_used": [],
        "production_messages_changed": False,
        "source_availability": source_availability,
        "summary": {
            "mean_estimated_tokens": round(mean(tokens), 1),
            "min_estimated_tokens": min(tokens),
            "max_estimated_tokens": max(tokens),
            "classification_counts": dict(sorted(classification_counts.items())),
            "generic_state_ratio": round(generic_ratio, 4),
            "empty_frame_count": sum(item["empty_frame"] for item in public_scenes),
            "generic_only_frame_count": sum(item["generic_only_frame"] for item in public_scenes),
            "non_structured_handover_entries": sum(
                item["source_metrics"]["handover"]["unstructured_handover_entries"]
                for item in public_scenes
            ),
            "single_pre_section_checkpoint_reused_for_four_subsections": True,
        },
        "mechanical_checks": mechanical,
        "scenes": public_scenes,
        "diagnosis": diagnosis,
        "minimum_upstream_contract": [
            "state_id", "predicate", "subject", "value", "epistemic_status",
            "effective_from", "effective_until", "section", "subsection",
            "source_id", "text_hash",
        ] if diagnosis == "upstream_state_contract_required" else [],
        "limitations": [
            "The frozen artifact contains one pre-section checkpoint, not four post-subsection state snapshots.",
            "WorldState facts expose category and verified flags but no explicit StateFrame predicate.",
            "Handover state is stored as unstructured strings inside a structured envelope.",
            "No generated candidate, arm mapping, human review, or evaluation result was read.",
        ],
        "contains_story_text": False,
    }
    private = {
        **public,
        "scenes": private_scenes,
        "private_values_included": False,
    }
    return public, private


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    source_bytes = args.source.read_bytes()
    source = json.loads(source_bytes.decode("utf-8"))
    public, private = build_audit(source, hashlib.sha256(source_bytes).hexdigest())
    args.runtime_dir.mkdir(parents=True, exist_ok=True)
    (args.runtime_dir / "audit.private.json").write_text(
        json.dumps(private, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "diagnosis": public["diagnosis"],
        "summary": public["summary"],
        "mechanical_checks": public["mechanical_checks"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
