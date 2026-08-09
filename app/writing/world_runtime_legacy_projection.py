"""WR3.1 projection adapter: WR CanonicalWorldState -> legacy StateFrame V1 facts.

Zero production wiring.  This module is the single place that maps WR facts
(subject/predicate/value/epistemic_status) onto the legacy StateFrame V1 fact
schema.  Event-only ledger entries (storefront sale/handoff) are NOT projected
into StateFrame facts in v1: they remain in the WR Event Ledger and are listed
separately by the projection report.
"""

from __future__ import annotations

from typing import Any, Literal

from .state_frame_v1 import (
    StateFact,
    StateFrameSnapshot,
    StateSourceRef,
    canonical_hash,
    task_id_hash,
)


LEGACY_PROJECTION_VERSION = "world-runtime-legacy-projection-wr3.1-v1"

# (subject_exact_or_prefix, predicate) -> (legacy fact_type, mapping_kind)
_PREDICATE_MAPPING: dict[tuple[str, str], tuple[str, str]] = {
    ("world_clock", "time"): ("temporal_state", "exact"),
    ("world_clock", "weekday"): ("temporal_state", "exact"),
    ("bakery:wild-bread:storefront", "operation_state"): ("presence_state", "exact"),
    ("bakery:wild-bread:workshop", "access_state"): ("presence_state", "exact"),
    ("bakery:wild-bread:workshop", "light"): ("presence_state", "exact"),
    ("character:", "location"): ("location_state", "exact"),
    ("character:", "article_knowledge"): ("character_state", "exact"),
    ("employment:", "status"): ("character_state", "approximate"),
    ("article:", "publication_state"): ("continuity_state", "approximate"),
    ("article:", "public_comment_count"): ("continuity_state", "approximate"),
    ("bakery:", "open_days"): ("continuity_state", "approximate"),
    ("bakery:", "opens_at"): ("temporal_state", "approximate"),
    ("bakery:", "production_starts_at"): ("temporal_state", "approximate"),
    ("company:", "resignation_acknowledged"): ("continuity_state", "approximate"),
    ("resignation:", "lifecycle_state"): ("continuity_state", "approximate"),
    ("object:", "content_state"): ("continuity_state", "approximate"),
    ("object:", "temperature_state"): ("continuity_state", "approximate"),
    ("object:", "location_state"): ("location_state", "approximate"),
}


def legacy_fact_mapping(subject: str, predicate: str) -> tuple[str, str, str] | None:
    """Return (legacy fact_type, legacy predicate, mapping_kind) or None."""
    exact = _PREDICATE_MAPPING.get((subject, predicate))
    if exact is not None:
        return (exact[0], predicate, exact[1])
    for prefix in (
        "character:", "employment:", "article:", "bakery:",
        "company:", "resignation:", "object:",
    ):
        if subject.startswith(prefix):
            hit = _PREDICATE_MAPPING.get((prefix, predicate))
            if hit is not None:
                return (hit[0], predicate, hit[1])
    return None


def _fact_status(epistemic_status: str) -> Literal["confirmed", "unknown"]:
    return "confirmed" if epistemic_status == "confirmed_true" else "unknown"


def _state_fact(fact, mapping: tuple[str, str, str], state_hash: str) -> StateFact:
    fact_type, _, _kind = mapping
    status = _fact_status(fact.epistemic_status)
    return StateFact(
        fact_id=f"legacy:{fact.fact_id}",
        fact_type=fact_type,
        subject=fact.subject,
        predicate=fact.predicate,
        value=fact.value,
        status=status,
        durability="persistent" if status == "confirmed" else "subsection",
        source_type="world_runtime_canonical_state",
        source_id=fact.fact_id,
        source_hash=state_hash,
        producer="world_runtime_legacy_projection",
        confidence=1.0 if status == "confirmed" else 0.0,
        provenance=f"wr3.1|{fact.fact_id}|{fact.revision}",
    )


def project_world_state(state, ledger=None) -> dict[str, Any]:
    """Project one WR canonical state into legacy StateFrame V1 facts."""
    state_hash = canonical_hash(state.model_dump(mode="json"))
    mapped: list[StateFact] = []
    unmapped: list[dict[str, Any]] = []
    for fact in state.facts:
        mapping = legacy_fact_mapping(fact.subject, fact.predicate)
        if mapping is None:
            unmapped.append({
                "fact_id": fact.fact_id,
                "subject": fact.subject,
                "predicate": fact.predicate,
                "value": fact.value,
                "epistemic_status": fact.epistemic_status,
            })
            continue
        mapped.append(_state_fact(fact, mapping, state_hash))
    event_only: list[dict[str, Any]] = []
    if ledger is not None:
        event_only = [
            {
                "revision": entry.revision,
                "change_type": entry.change_type,
                "subject": entry.subject,
                "predicate": entry.predicate,
                "after_value": entry.after_value,
                "evidence_ids": list(entry.evidence_ids),
            }
            for entry in ledger.entries
            if entry.fact_id is None
        ]
    exact_count = sum(
        1 for fact in state.facts
        if (mapping := legacy_fact_mapping(fact.subject, fact.predicate)) is not None
        and mapping[2] == "exact"
    )
    return {
        "schema_version": LEGACY_PROJECTION_VERSION,
        "task_id_hash": task_id_hash(str(state.project_id)),
        "state_hash": state_hash,
        "revision": state.revision,
        "facts": [fact.model_dump(mode="json") for fact in mapped],
        "unmapped_facts": unmapped,
        "event_only_entries": event_only,
        "coverage": {
            "fact_count": len(state.facts),
            "mapped_count": len(mapped),
            "unmapped_count": len(unmapped),
            "exact_mapping_count": exact_count,
            "approximate_mapping_count": len(mapped) - exact_count,
            "event_only_entry_count": len(event_only),
        },
    }


def project_state_frame(
    committed,
    *,
    task_id: str = "saturday-bakery-canary",
    section: int = 1,
    subsection: int = 1,
) -> StateFrameSnapshot:
    """Compile the legacy StateFrame V1 after-view from one WR commit.

    The legacy snapshot is the authoritative downstream view for WR3.2+: it is
    populated from the adapter projection (16/16 facts), unlike the WR-internal
    contracts.StateFrame whose category vocabulary excludes WR predicates.
    """
    projection = project_world_state(committed.after, ledger=committed.ledger)
    facts = tuple(StateFact(**item) for item in projection["facts"])
    source_manifest = tuple(
        StateSourceRef(
            source_type="world_runtime_canonical_state",
            source_id=fact.fact_id,
            source_hash=projection["state_hash"],
            producer="world_runtime_legacy_projection",
            section=section,
            subsection=subsection,
        )
        for fact in committed.after.facts
    )
    frame = StateFrameSnapshot(
        frame_id=f"legacy-frame:{committed.commit_id}",
        task_id_hash=task_id_hash(task_id),
        section=section,
        subsection=subsection,
        frame_phase="after_commit",
        frame_status="complete",
        facts=facts,
        source_manifest=source_manifest,
        frame_hash="",
    )
    payload = frame.model_dump(mode="json")
    payload.pop("frame_hash")
    frame_hash = canonical_hash(payload)
    return frame.model_copy(update={"frame_hash": frame_hash})
