"""WR3.9 post-run corrected comparison (offline, read-only).

The contract runner's compare under-fed legacy post-write bundles (expected
``[{"bundle": ...}]``).  This diagnostic rebuilds the legacy StateFrame V1 with
the correct input shape from the frozen outputs, so the WR3.9 divergence report
can be interpreted honestly.  It makes no LLM calls and writes only a report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.writing.state_frame_service import build_state_frame_artifacts
from app.writing.world_runtime_handover_projection import project_handover
from app.writing.world_runtime_legacy_projection import project_state_frame
from app.writing.world_runtime_state_committer import CommittedWorldState


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".world_runtime_wr39_dual_chain_runtime"
CANARY_COMMITS = ROOT / ".world_runtime_state_commit_canary_runtime/c21r10/private/commits"
OUTPUT = ROOT / "reports" / "wr39-dual-chain-corrected-2026-08-07.json"

CHARACTERS = [
    {"name": "林晚", "personality": "细致"},
    {"name": "周野", "personality": "专注"},
    {"name": "季晴", "personality": "敏锐"},
    {"name": "老吴", "personality": "热心"},
]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_frame_facts(subsection: int) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
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
    after = artifacts["after"]
    facts = [
        {
            "fact_type": fact.get("fact_type", ""),
            "subject": fact.get("subject", ""),
            "predicate": fact.get("predicate", ""),
            "value": fact.get("value"),
            "status": fact.get("status", ""),
        }
        for fact in after.get("facts", [])
    ]
    return facts, after.get("unavailable_source_types", []), note


def _wr_frame_facts(commit: dict[str, Any], subsection: int) -> list[dict[str, Any]]:
    committed = CommittedWorldState.model_validate(commit)
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


def _key(fact: dict[str, Any]) -> tuple[str, str, str]:
    return (fact["fact_type"], fact["subject"], fact["predicate"])


def _handover_coverage(note: dict[str, Any], wr_note: dict[str, Any]) -> dict[str, Any]:
    return {
        field: {
            "legacy_non_empty": bool(note.get(field)),
            "wr_non_empty": bool(wr_note.get(field)),
        }
        for field in (
            "foreshadowing", "character_state", "open_threads",
            "new_facts", "found_contradictions", "arc_progress",
        )
    }


def main() -> None:
    reports = []
    totals = {"subsections": 0, "matched": 0, "wr_only": 0, "legacy_only": 0, "value_mismatch": 0}
    for subsection in range(1, 4):
        legacy_facts, unavailable, note = _legacy_frame_facts(subsection)
        wr_facts = _wr_frame_facts(_read_json(CANARY_COMMITS / f"S{subsection}.json"), subsection)
        legacy_by_key = {_key(fact): fact for fact in legacy_facts}
        wr_by_key = {_key(fact): fact for fact in wr_facts}
        matched, value_mismatch = [], []
        for key, wr_fact in wr_by_key.items():
            legacy_fact = legacy_by_key.get(key)
            if legacy_fact is None:
                continue
            if json.dumps(wr_fact["value"], ensure_ascii=False, sort_keys=True) == json.dumps(
                legacy_fact["value"], ensure_ascii=False, sort_keys=True
            ):
                matched.append(key)
            else:
                value_mismatch.append({"key": list(key), "wr": wr_fact["value"], "legacy": legacy_fact["value"]})
        wr_only = [list(key) for key in wr_by_key.keys() - legacy_by_key.keys()]
        legacy_only = [list(key) for key in legacy_by_key.keys() - wr_by_key.keys()]
        commit = _read_json(CANARY_COMMITS / f"S{subsection}.json")
        committed = CommittedWorldState.model_validate(commit)
        wr_handover = project_handover(committed)
        reports.append({
            "subsection": subsection,
            "legacy_frame_fact_count": len(legacy_facts),
            "wr_frame_fact_count": len(wr_facts),
            "matched_fact_keys": len(matched),
            "wr_only_fact_keys": wr_only,
            "legacy_only_fact_keys": legacy_only,
            "value_mismatches": value_mismatch,
            "legacy_unavailable_sources": unavailable,
            "handover_coverage": _handover_coverage(note, wr_handover["note"]),
            "wr_clock_values": [
                str(e["after_value"]) for e in commit["ledger"]["entries"]
                if e["change_type"] == "clock_state"
            ],
            "legacy_temporal_values": sorted({
                str(f["value"]) for f in legacy_facts
                if f["fact_type"] == "temporal_state" and f["value"] is not None
            }),
        })
        totals["subsections"] += 1
        totals["matched"] += len(matched)
        totals["wr_only"] += len(wr_only)
        totals["legacy_only"] += len(legacy_only)
        totals["value_mismatch"] += len(value_mismatch)
    result = {
        "schema_version": "wr39-corrected-comparison-v1",
        "note": "post-run diagnostic; corrects legacy post-write bundle ingestion",
        "totals": totals,
        "subsections": reports,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
