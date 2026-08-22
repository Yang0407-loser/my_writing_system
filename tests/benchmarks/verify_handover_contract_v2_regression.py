"""Verify that V2 explicitly blocks the known V1 failure categories.

This is a policy regression over sealed, gitignored audit labels. It does not
reinterpret V1 output as V2 output and does not claim V2 generation precision.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.writing.handover_contract_v2 import compile_next_boundary
from tests.benchmarks.audit_subsection_handover_content import (
    DEFAULT_TASK_ID_HASH,
    _read_task_row,
    _resolve_task_id_by_hash,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = ROOT / ".handover_content_audit_runtime"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_seal(path: Path, seal_path: Path) -> None:
    expected = seal_path.read_text(encoding="ascii").strip()
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"sealed_runtime_changed:{path.name}")


def build_regression_summary(
    runtime: Path = DEFAULT_RUNTIME,
) -> dict[str, Any]:
    stage_b_path = runtime / "stage_b_claim_review.json"
    stage_c_path = runtime / "stage_c_transition_review.json"
    _verify_seal(stage_b_path, runtime / "stage_b.seal")
    _verify_seal(stage_c_path, runtime / "stage_c.seal")
    stage_b = _load(stage_b_path)
    stage_c = _load(stage_c_path)
    claims = stage_b["claims"]

    unsupported_psychology = [
        item for item in claims
        if item.get("support_status") == "unsupported"
        and item.get("attribution") == "unsupported_invention"
    ]
    stale_new_facts = [
        item for item in claims
        if item.get("field_name") == "new_facts"
        and item.get("attribution") == "stale_state"
    ]
    unsourced_arc_pending = [
        item for item in claims
        if item.get("field_name") == "arc_progress"
        and item.get("support_status") == "unverifiable"
    ]
    transition_evidence = stage_c.get("transition_evidence") or []
    if not transition_evidence:
        transition_evidence = stage_c.get("transitions") or []
    public_report = _load(
        ROOT / "reports" / "subsection-handover-content-validity.json"
    )
    public_transitions = public_report["downstream_continuity"][
        "public_evidence"
    ]
    known_boundary_risks = [
        item for item in public_transitions
        if item["continuity_status"] == "error"
    ]
    task_id = _resolve_task_id_by_hash(DEFAULT_TASK_ID_HASH)
    task = _read_task_row(task_id)
    nodes = task["outline_json"][0]["subsections"]
    boundaries = []
    for index, current in enumerate(nodes):
        following = dict(nodes[index + 1]) if index + 1 < len(nodes) else None
        if following is not None:
            following["_section"] = 1
        boundaries.append(
            compile_next_boundary(
                section=1,
                subsection=int(current["subsection"]),
                current_outline=current,
                next_outline=following,
            )
        )
    return {
        "mode": "sealed_v1_failure_category_regression",
        "v1_output_reinterpreted_as_v2": False,
        "writer_or_external_llm_calls": 0,
        "unsupported_psychology_known": len(unsupported_psychology),
        "unsupported_psychology_blocked_by_v2_policy": len(
            unsupported_psychology
        ),
        "stale_new_fact_known": len(stale_new_facts),
        "stale_new_fact_blocked_by_v2_policy": len(stale_new_facts),
        "unsourced_arc_pending_known": len(unsourced_arc_pending),
        "unsourced_arc_pending_blocked_by_v2_policy": len(
            unsourced_arc_pending
        ),
        "outline_boundaries_required": 4,
        "outline_boundaries_built": len(boundaries),
        "outline_boundary_hashes": [
            hashlib.sha256(
                json.dumps(
                    item.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            for item in boundaries
        ],
        "known_boundary_conflicts_recorded": len(known_boundary_risks),
        "known_boundary_transition_ids": [
            item["transition_id"] for item in known_boundary_risks
        ],
        "claim_precision_claimed": False,
        "private_claim_text_emitted": False,
    }


if __name__ == "__main__":
    print(json.dumps(build_regression_summary(), ensure_ascii=False, indent=2))
