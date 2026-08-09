"""WR3.9 semantic divergence recomputation (offline, read-only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.writing.state_frame_service import build_state_frame_artifacts
from app.writing.world_runtime_legacy_projection import project_state_frame
from app.writing.world_runtime_state_committer import CommittedWorldState
from experiments.world_runtime_writer_canary.wr39_semantic_mapping import (
    semantic_compare,
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".world_runtime_wr39_dual_chain_runtime"
CANARY_COMMITS = ROOT / ".world_runtime_state_commit_canary_runtime/c21r10/private/commits"
OUTPUT = ROOT / "reports" / "wr39-dual-chain-semantic-2026-08-07.json"

CHARACTERS = [
    {"name": "林晚", "personality": "细致"},
    {"name": "周野", "personality": "专注"},
    {"name": "季晴", "personality": "敏锐"},
    {"name": "老吴", "personality": "热心"},
]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_frame_facts(subsection: int) -> list[dict[str, Any]]:
    note_payload = _read_json(RUNTIME / "private/outputs" / f"S{subsection}.handover.json")
    note = dict(note_payload.get("note") or {})
    note["to_section"] = subsection
    bundle = _read_json(RUNTIME / "private/outputs" / f"S{subsection}.bundle.json")
    artifacts = build_state_frame_artifacts(
        task_id="c21r10-dual-chain",
        section=1,
        subsection=subsection,
        task_data={
            "handover_notes": [note],
            "post_write_extraction_shadow": [{"bundle": bundle}],
            "characters": CHARACTERS,
        },
    )
    return [
        {
            "fact_type": fact.get("fact_type", ""),
            "subject": fact.get("subject", ""),
            "predicate": fact.get("predicate", ""),
            "value": fact.get("value"),
            "status": fact.get("status", ""),
        }
        for fact in artifacts["after"].get("facts", [])
    ]


def _wr_frame_facts(subsection: int) -> list[dict[str, Any]]:
    committed = CommittedWorldState.model_validate(
        _read_json(CANARY_COMMITS / f"S{subsection}.json")
    )
    frame = project_state_frame(committed, task_id="c21r10-dual-chain", section=1, subsection=subsection)
    return [
        {
            "fact_type": fact.fact_type,
            "subject": fact.subject,
            "predicate": fact.predicate,
            "value": fact.value,
            "status": fact.status,
        }
        for fact in frame.facts
    ]


def main() -> None:
    subsections = []
    totals = {
        "legacy_fact_count": 0,
        "legacy_mapped_count": 0,
        "legacy_unmapped_by_design_count": 0,
        "wr_fact_count": 0,
        "matched_fact_keys": 0,
        "value_mismatch_count": 0,
        "wr_only_fact_keys": 0,
        "legacy_only_mapped_fact_keys": 0,
    }
    for subsection in range(1, 4):
        legacy_facts = _legacy_frame_facts(subsection)
        wr_facts = _wr_frame_facts(subsection)
        result = semantic_compare(legacy_facts, wr_facts)
        result["subsection"] = subsection
        subsections.append(result)
        for key in totals:
            if key == "legacy_only_mapped_fact_keys":
                totals[key] += len(result[key])
            elif key == "wr_only_fact_keys":
                totals[key] += len(result[key])
            else:
                totals[key] += result[key]
    report = {
        "schema_version": "wr39-semantic-comparison-v1",
        "totals": totals,
        "subsections": subsections,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
