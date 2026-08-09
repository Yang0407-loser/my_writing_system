from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import BoundaryTicket, LockedBoundaries


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def contract_hash(fixture: dict[str, Any]) -> str:
    keys = ("scene", "characters", "world_facts", "mandatory_events", "forbidden_events", "boundary_space")
    return canonical_hash({key: fixture[key] for key in keys})


def build_ticket(fixture: dict[str, Any], repeat: int, handling: str) -> BoundaryTicket:
    boundaries = LockedBoundaries(
        priority_object="customer_field_diary",
        store_item_temporary_handling=handling,
        long_term_problem="unresolved",
        relationship_delta="none",
        new_characters="none",
        new_solution="none",
    )
    body = {
        "schema_version": "1.1", "scene_id": "SC4", "repeat": repeat,
        "source_contract_hash": contract_hash(fixture),
        "locked_boundaries": boundaries.model_dump(),
        "content_facts_added": [], "locked": True,
    }
    return BoundaryTicket.model_validate({**body, "ticket_hash": canonical_hash(body)})


def validate_ticket(ticket: BoundaryTicket, fixture: dict[str, Any]) -> BoundaryTicket:
    if ticket.source_contract_hash != contract_hash(fixture):
        raise ValueError("source contract hash mismatch")
    if ticket.ticket_hash != canonical_hash(ticket.model_dump(exclude={"ticket_hash"})):
        raise ValueError("ticket hash mismatch")
    return ticket


def mock_ticket(fixture: dict[str, Any], repeat: int) -> BoundaryTicket:
    return build_ticket(fixture, repeat, ("raised_mesh_rack", "single_absorbent_wrap")[repeat - 1])

