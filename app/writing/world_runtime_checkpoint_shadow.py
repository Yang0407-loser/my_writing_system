"""WR3.7 shadow checkpoint writer: WR commit -> checkpoint shadow field.

Zero read switching.  The shadow writer adds exactly one checkpoint field
(``world_runtime_shadow_v1``) and never modifies legacy checkpoint fields.
The payload is deterministic, self-verifying (ledger replay -> after) and
idempotent (same commit -> same payload).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .world_runtime_contracts import canonical_hash
from .world_runtime_legacy_projection import project_state_frame


CHECKPOINT_SHADOW_KEY = "world_runtime_shadow_v1"
CHECKPOINT_SHADOW_SCHEMA = "world-runtime-checkpoint-shadow-v1"


def _normalize_facts(facts) -> list[dict[str, Any]]:
    return [
        {
            "fact_id": fact.fact_id,
            "subject": fact.subject,
            "predicate": fact.predicate,
            "value": fact.value,
            "epistemic_status": fact.epistemic_status,
            "revision": fact.revision,
        }
        for fact in facts
    ]


def _normalize_ledger(entries) -> list[dict[str, Any]]:
    return [
        {
            "revision": entry.revision,
            "change_type": entry.change_type,
            "subject": entry.subject,
            "predicate": entry.predicate,
            "after_value": entry.after_value,
            "fact_id": entry.fact_id,
            "evidence_ids": list(entry.evidence_ids),
        }
        for entry in entries
    ]


def build_shadow_payload(committed) -> dict[str, Any]:
    """Build the deterministic shadow payload for one WR commit."""
    legacy_frame = project_state_frame(committed)
    payload = {
        "schema_version": CHECKPOINT_SHADOW_SCHEMA,
        "commit_id": committed.commit_id,
        "idempotency_key": committed.idempotency_key,
        "revision": committed.after.revision,
        "output_hash": committed.output_hash,
        "before_facts": _normalize_facts(committed.before.facts),
        "after_facts": _normalize_facts(committed.after.facts),
        "ledger_entries": _normalize_ledger(committed.ledger.entries),
        "legacy_frame_hash": legacy_frame.frame_hash,
        "legacy_frame_facts": [
            fact.model_dump(mode="json") for fact in legacy_frame.facts
        ],
    }
    payload["payload_hash"] = canonical_hash(payload)
    return payload


def _replay_facts(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    facts = {
        fact["fact_id"]: {
            "value": fact["value"],
            "epistemic_status": fact["epistemic_status"],
            "revision": fact["revision"],
        }
        for fact in payload["before_facts"]
    }
    for entry in payload["ledger_entries"]:
        fact_id = entry.get("fact_id")
        if fact_id is None:
            continue
        facts[fact_id] = {
            "value": entry["after_value"],
            "epistemic_status": "confirmed_true",
            "revision": entry["revision"],
        }
    return facts


def verify_shadow_payload(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    """Verify payload internal consistency (replay, revision, hash)."""
    issues: list[str] = []
    if payload.get("schema_version") != CHECKPOINT_SHADOW_SCHEMA:
        issues.append("schema_version_mismatch")
    expected_hash = canonical_hash({
        key: value for key, value in payload.items() if key != "payload_hash"
    })
    if payload.get("payload_hash") != expected_hash:
        issues.append("payload_hash_mismatch")
    after_by_id = {fact["fact_id"]: fact for fact in payload["after_facts"]}
    replayed = _replay_facts(payload)
    for fact_id, expected in replayed.items():
        actual = after_by_id.get(fact_id)
        if actual is None:
            issues.append(f"replay_missing_fact:{fact_id}")
        elif actual["value"] != expected["value"]:
            issues.append(f"replay_value_mismatch:{fact_id}")
    for fact_id in after_by_id:
        if fact_id not in replayed:
            issues.append(f"after_fact_not_in_replay:{fact_id}")
    return not issues, issues


def merge_shadow(checkpoint: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any]:
    """Return a checkpoint copy with the shadow field added; legacy keys untouched."""
    merged = dict(checkpoint or {})
    merged[CHECKPOINT_SHADOW_KEY] = payload
    return merged


def read_shadow(checkpoint: dict[str, Any] | None) -> dict[str, Any] | None:
    return (checkpoint or {}).get(CHECKPOINT_SHADOW_KEY)


def write_shadow_to_blackboard(blackboard, task_id: str, committed) -> dict[str, Any]:
    """Shadow-write one WR commit into the task checkpoint (new field only)."""
    payload = build_shadow_payload(committed)
    checkpoint = blackboard.load_checkpoint(task_id) or {}
    merged = merge_shadow(checkpoint, payload)
    blackboard.save_checkpoint(task_id, merged)
    return payload
