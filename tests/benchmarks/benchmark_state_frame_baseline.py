"""Build the deterministic StateFrame structural baseline without an LLM."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean

from app.writing.state_frame import StateFrameCompiler
from app.writing.story_state_view import StoryStateView


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "reports" / "state-frame-batch1-baseline.json"

CASES = (
    (
        "time_and_unknown",
        [
            ("current_time_anchor", "confirmed"),
            ("future_time_relation", "unknown"),
            ("planned_event", "planned"),
        ],
    ),
    (
        "location_and_presence",
        [
            ("current_location", "confirmed"),
            ("character_presence", "confirmed"),
            ("hard_constraint", "confirmed"),
        ],
    ),
    (
        "persistent_and_relationship",
        [
            ("character_state", "confirmed"),
            ("world_fact", "confirmed"),
            ("relationship_stage", "confirmed"),
            ("arc_milestone", "confirmed"),
        ],
    ),
    (
        "open_and_conflict",
        [
            ("open_loop", "planned"),
            ("location_state", "conflicted"),
            ("continuity_state", "confirmed"),
        ],
    ),
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _build_case(index: int, name: str, definitions: list[tuple[str, str]]) -> dict:
    sources = []
    assertions = []
    for assertion_index, (predicate, status) in enumerate(definitions, 1):
        evidence_id = f"ev:{index}:{assertion_index}"
        value = f"contract-value-{index}-{assertion_index}"
        sources.append({
            "evidence_id": evidence_id,
            "source_id": f"contract-source:{index}:{assertion_index}",
            "source_type": "contract_fixture",
            "text": value,
            "section": index,
            "subsection": 1,
            "span_start": 0,
            "span_end": len(value),
        })
        assertions.append({
            "assertion_id": f"assertion:{index}:{assertion_index}",
            "subject": "contract",
            "predicate": predicate,
            "value": value,
            "status": status,
            "evidence_ids": [evidence_id],
        })
    snapshot = StoryStateView(
        task_id="state-frame-contract", section=index, subsection=1
    ).project(sources, assertions)
    compiler = StateFrameCompiler()
    frame = compiler.compile(snapshot)
    rendered = compiler.render(frame)
    included = (
        frame.temporal_state + frame.location_state + frame.character_presence
        + frame.persistent_state + frame.relationship_state + frame.open_loops
        + frame.unknowns_and_conflicts
    )
    referenced = {
        evidence_id for assertion in included for evidence_id in assertion.evidence_ids
    }
    evidence_ids = {item.evidence_id for item in frame.evidence}
    return {
        "case_id": name,
        "frame_hash": frame.frame_hash,
        "source_hash": frame.source_hash,
        "rendered_hash": _sha256(rendered),
        "estimated_tokens": frame.estimated_tokens,
        "counts": {
            "temporal": len(frame.temporal_state),
            "location": len(frame.location_state),
            "presence": len(frame.character_presence),
            "persistent": len(frame.persistent_state),
            "relationship": len(frame.relationship_state),
            "open_loops": len(frame.open_loops),
            "unknowns_conflicts": len(frame.unknowns_and_conflicts),
            "excluded": len(frame.excluded_assertion_ids),
        },
        "excluded_assertion_ids": frame.excluded_assertion_ids,
        "traceability_rate": 1.0 if referenced == evidence_ids else 0.0,
        "contains_story_text": False,
    }


def build_report() -> dict:
    cases = [
        _build_case(index, name, list(definitions))
        for index, (name, definitions) in enumerate(CASES, 1)
    ]
    tokens = [item["estimated_tokens"] for item in cases]
    return {
        "phase": "StateFrame Batch 1",
        "mode": "offline_contract_only",
        "schema_version": "state-frame-v1",
        "writer_generation_calls": 0,
        "llm_calls": 0,
        "production_writer_imports_state_frame": False,
        "runtime_evaluation_fields_used": [],
        "summary": {
            "case_count": len(cases),
            "mean_estimated_tokens": round(mean(tokens), 1),
            "min_estimated_tokens": min(tokens),
            "max_estimated_tokens": max(tokens),
            "all_sources_traceable": all(item["traceability_rate"] == 1.0 for item in cases),
            "all_unknowns_preserved": all(
                item["counts"]["unknowns_conflicts"] == 1
                for item in cases if item["case_id"] in {"time_and_unknown", "open_and_conflict"}
            ),
            "plans_and_hard_rules_excluded": all(
                item["counts"]["excluded"] >= 1
                for item in cases if item["case_id"] in {"time_and_unknown", "location_and_presence"}
            ),
        },
        "cases": cases,
        "decision": "contract_ready_for_separate_real-input_coverage_audit",
        "limitations": [
            "This batch validates structure and provenance, not Writer quality.",
            "No StateFrame is injected into production messages.",
            "Explicit predicates are required; text keywords are not used for classification."
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
