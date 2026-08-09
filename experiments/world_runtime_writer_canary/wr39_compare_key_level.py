"""WR3.9+ key-level semantic divergence (offline, read-only).

Upgrade of ``wr39_compare_semantic.py``: per-fact key matrix with mapping kind
and value status, plus reverse WR-key coverage.  Writes a new frozen report
file; the v1 report is left untouched.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.writing.state_frame_service import build_state_frame_artifacts
from app.writing.world_runtime_legacy_projection import project_state_frame
from app.writing.world_runtime_state_committer import CommittedWorldState
from experiments.world_runtime_writer_canary.wr39_semantic_mapping import (
    key_level_compare,
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".world_runtime_wr39_dual_chain_runtime"
CANARY_COMMITS = ROOT / ".world_runtime_state_commit_canary_runtime/c21r10/private/commits"
OUTPUT = ROOT / "reports" / "wr39-key-level-semantic-2026-08-07.json"

CHARACTERS = [
    {"name": "林晚", "personality": "细致"},
    {"name": "周野", "personality": "专注"},
    {"name": "季晴", "personality": "敏锐"},
    {"name": "老吴", "personality": "热心"},
]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_frame_facts(subsection: int) -> list[dict[str, Any]]:
    note_payload = _read_json(
        RUNTIME / "private/outputs" / f"S{subsection}.handover.json"
    )
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
    frame = project_state_frame(
        committed,
        task_id="c21r10-dual-chain",
        section=1,
        subsection=subsection,
    )
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
    totals: dict[str, Any] = {
        "legacy_fact_count": 0,
        "legacy_mapped_count": 0,
        "legacy_unmapped_by_design_count": 0,
        "wr_fact_count": 0,
        "wr_covered_key_instance_count": 0,
        "wr_only_key_instance_count": 0,
    }
    status_totals: Counter[str] = Counter()
    covered_anywhere: set[tuple[str, str, str]] = set()
    all_wr_keys: set[tuple[str, str, str]] = set()

    for subsection in range(1, 4):
        legacy_facts = _legacy_frame_facts(subsection)
        wr_facts = _wr_frame_facts(subsection)
        result = key_level_compare(legacy_facts, wr_facts)
        result["subsection"] = subsection
        subsections.append(result)
        summary = result["summary"]
        for key in (
            "legacy_fact_count",
            "legacy_mapped_count",
            "legacy_unmapped_by_design_count",
            "wr_fact_count",
        ):
            totals[key] += summary[key]
        totals["wr_covered_key_instance_count"] += summary["wr_covered_key_count"]
        totals["wr_only_key_instance_count"] += summary["wr_only_key_count"]
        status_totals.update(summary["status_counts"])
        for row in result["wr_coverage"]:
            key = tuple(row["wr_key"])
            all_wr_keys.add(key)
            if row["covered"]:
                covered_anywhere.add(key)

    totals["status_counts"] = dict(status_totals)
    totals["wr_covered_unique_key_count"] = len(covered_anywhere)
    totals["wr_only_unique_key_count"] = len(all_wr_keys - covered_anywhere)
    totals["wr_only_unique_keys"] = sorted(
        list(key) for key in all_wr_keys - covered_anywhere
    )

    report = {
        "schema_version": "wr39-key-level-semantic-v2",
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
