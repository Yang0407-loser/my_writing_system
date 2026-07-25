"""Deterministic output-capacity accounting for Handover V2 and V2.1."""

from __future__ import annotations

import json
from typing import Any

from app.utils.llm_client import estimate_tokens
from app.writing.handover_contract_v2 import canonical_json
from app.writing.handover_contract_v21 import (
    compact_payload_metrics,
    typical_compact_payload,
    worst_legal_compact_payload,
)


def _evidence(index: int) -> dict[str, Any]:
    return {
        "source_type": "generated_subsection",
        "source_id": f"generated-subsection:S1.{index}",
        "source_hash": str(index) * 64,
        "start": index * 20,
        "end": index * 20 + 16,
        "excerpt": "这是一段精确的原文证据",
    }


def _claim(index: int) -> dict[str, Any]:
    return {
        "claim_id": f"claim-{index}",
        "category": "character_state",
        "subject": "林晚",
        "predicate": "继续记录凌晨见闻",
        "object": "笔记本",
        "temporal_status": "current",
        "certainty": "confirmed",
        "evidence": [_evidence(index)],
        "claim_hash": "",
        "provenance": "handover_extractor_v2",
    }


def _open_event(index: int) -> dict[str, Any]:
    evidence = _evidence(index)
    return {
        "event_id": f"open-{index}",
        "actors": ["林晚", "周野"],
        "action": "等待回应",
        "object": "再次见面的邀请",
        "completion_status": "open",
        "evidence": [evidence],
        "source_hash": evidence["source_hash"],
    }


def _arc(index: int) -> dict[str, Any]:
    return {
        "character_id": "linwan",
        "event_id": f"milestone-{index}",
        "completion_status": "partially_completed",
        "milestone_source_id": f"arc-milestone:{index}",
        "milestone_source_hash": "a" * 64,
        "evidence": [_evidence(index)],
    }


def _metrics(payload: dict[str, Any]) -> dict[str, int]:
    encoded = canonical_json(payload)
    return {"characters": len(encoded), "estimated_tokens": estimate_tokens(encoded)}


def build_capacity_report() -> dict[str, Any]:
    v2_minimum = {"claims": [], "open_events": [], "arc_progress": []}
    v2_typical = {
        "claims": [_claim(1), _claim(2)],
        "open_events": [_open_event(1)],
        "arc_progress": [_arc(1)],
    }
    v2_worst = {
        "claims": [_claim(index) for index in range(1, 5)],
        "open_events": [_open_event(index) for index in range(1, 4)],
        "arc_progress": [_arc(index) for index in range(1, 3)],
    }
    return {
        "estimator": "app.utils.llm_client.estimate_tokens",
        "v2_output": {
            "minimum": _metrics(v2_minimum),
            "typical": _metrics(v2_typical),
            "worst_representative": _metrics(v2_worst),
            "next_boundary_output_tokens": 0,
            "accepted_rejected_output_tokens": 0,
        },
        "v21_output": {
            "typical": compact_payload_metrics(typical_compact_payload()),
            "worst_legal": compact_payload_metrics(worst_legal_compact_payload()),
            "output_cap": 600,
        },
        "v2_repetition": {
            "source_id_occurrences_worst": 9,
            "source_hash_occurrences_worst": 14,
            "evidence_excerpt_occurrences_worst": 9,
            "long_named_enum_occurrences_worst": 27,
            "boundary_already_local_but_in_prompt": True,
        },
        "writer_or_external_llm_calls": 0,
    }


if __name__ == "__main__":
    print(json.dumps(build_capacity_report(), ensure_ascii=False, indent=2))
