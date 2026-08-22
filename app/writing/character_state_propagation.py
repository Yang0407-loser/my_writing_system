"""Deterministic helpers for propagating character arc state safely."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def is_valid_character_arcs(value: Any) -> bool:
    """Accept the existing legacy shape without inventing a second schema."""
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def copy_character_arcs(value: Any) -> list[dict]:
    if not is_valid_character_arcs(value):
        raise ValueError("character_arcs must be a list of dictionaries")
    return copy.deepcopy(value)


def resolve_writer_character_arcs(
    writer_result: dict,
    fallback: list[dict] | None,
) -> tuple[list[dict], str]:
    """Resolve the Writer result while remaining compatible with old results."""
    candidate = writer_result.get("character_arcs")
    if "character_arcs" not in writer_result:
        return copy_character_arcs(fallback or []), "missing_writer_state"
    if not is_valid_character_arcs(candidate):
        return copy_character_arcs(fallback or []), "invalid_writer_state"
    return copy_character_arcs(candidate), "writer_updated"


def character_arcs_hash(value: list[dict] | None) -> str:
    payload = json.dumps(
        value or [],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def task_id_hash(task_id: str) -> str:
    return hashlib.sha256(str(task_id).encode("utf-8")).hexdigest()


def build_character_state_propagation_event(
    *,
    task_id: str,
    section: int | None,
    subsection: int | None,
    source: str,
    input_state_hash: str,
    updated_state_hash: str,
    coordinator_state_hash: str | None = None,
    checkpoint_state_hash: str | None = None,
    reviewer_state_hash: str | None = None,
    update_applied: bool,
    fallback_reason: str | None = None,
    checkpoint_version: str,
) -> dict:
    return {
        "event": "character_state_propagation",
        "task_id_hash": task_id_hash(task_id),
        "section": section,
        "subsection": subsection,
        "source": source,
        "input_state_hash": input_state_hash,
        "updated_state_hash": updated_state_hash,
        "coordinator_state_hash": coordinator_state_hash,
        "checkpoint_state_hash": checkpoint_state_hash,
        "reviewer_state_hash": reviewer_state_hash,
        "update_applied": update_applied,
        "fallback_reason": fallback_reason,
        "checkpoint_version": checkpoint_version,
        "production_effect": "character_state_propagation_only",
    }
