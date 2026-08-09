from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .models import DecisionTicket, SelectedDecisions


def canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def source_contract_hash(fixture: dict[str, Any]) -> str:
    keys = ("scene", "characters", "world_facts", "mandatory_events", "forbidden_events")
    return canonical_hash({key: fixture[key] for key in keys})


def build_ticket(
    fixture: dict[str, Any], repeat: int, selected: dict[str, Any]
) -> DecisionTicket:
    validated = SelectedDecisions.model_validate(selected)
    body = {
        "schema_version": "1.0",
        "scene_id": "SC3",
        "repeat": repeat,
        "source_contract_hash": source_contract_hash(fixture),
        "selected_decisions": validated.model_dump(),
        "content_facts_added": [],
        "locked": True,
    }
    return DecisionTicket.model_validate({**body, "ticket_hash": canonical_hash(body)})


def validate_ticket(ticket: DecisionTicket, fixture: dict[str, Any]) -> DecisionTicket:
    body = ticket.model_dump(exclude={"ticket_hash"})
    if ticket.source_contract_hash != source_contract_hash(fixture):
        raise ValueError("ticket source contract hash mismatch")
    if ticket.ticket_hash != canonical_hash(body):
        raise ValueError("ticket hash mismatch")
    return ticket


def frozen_snapshot(ticket: DecisionTicket) -> str:
    return json.dumps(
        copy.deepcopy(ticket.model_dump()),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def mock_ticket(fixture: dict[str, Any], repeat: int) -> DecisionTicket:
    choices = [
        {
            "initial_risk_check": "check_album_first",
            "catalog_temporary_handling": "manual_blotting",
            "expand_focus": ["adhesive_instability", "battery_limit"],
            "dialogue_jobs": [
                "risk_confirmation", "priority_choice",
                "responsibility_boundary", "open_ending",
            ],
            "emotion_channels": ["hesitation", "object_handling", "silence"],
            "ending_state": "temporary_only",
            "relationship_delta": "none",
            "new_characters": "none",
            "new_solution": "none",
        },
        {
            "initial_risk_check": "check_power_capacity_first",
            "catalog_temporary_handling": "sealed_dry_box",
            "expand_focus": ["battery_limit", "catalog_temporary_handling"],
            "dialogue_jobs": [
                "risk_confirmation", "priority_choice",
                "responsibility_boundary", "open_ending",
            ],
            "emotion_channels": ["waiting_for_confirmation", "unfinished_action"],
            "ending_state": "temporary_only",
            "relationship_delta": "none",
            "new_characters": "none",
            "new_solution": "none",
        },
    ]
    return build_ticket(fixture, repeat, choices[repeat - 1])

