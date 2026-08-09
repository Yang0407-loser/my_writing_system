"""WR3.3 projection: WR commit -> legacy handover note fields (offline).

Deterministic v1 projection of the six legacy handover fields
(HANDOVER_FIELD_NAMES) from a WR committed world state.  Fields with a WR
ontology source are populated; fields with no WR equivalent are explicitly
marked ``legacy_only_not_projected`` so the downstream migration gap stays
visible.  A true divergence run against ``Writer._extract_handover`` requires
a task where both chains run (separately authorized).
"""

from __future__ import annotations

from typing import Any

from .subsection_handover_history import HANDOVER_FIELD_NAMES


HANDOVER_PROJECTION_VERSION = "world-runtime-handover-projection-wr3.3-v1"

_CHARACTER_SUBJECT_PREFIXES = ("character:", "employment:")


def _fact_item(fact) -> dict[str, Any]:
    return {
        "subject": fact.subject,
        "predicate": fact.predicate,
        "value": fact.value,
        "epistemic_status": fact.epistemic_status,
        "revision": fact.revision,
    }


def project_handover(committed) -> dict[str, Any]:
    """Project one WR commit into the six legacy handover fields."""
    before_ids = {fact.fact_id for fact in committed.before.facts}
    before_by_id = {fact.fact_id: fact for fact in committed.before.facts}
    new_facts = [
        {
            "subject": entry.subject,
            "predicate": entry.predicate,
            "after_value": entry.after_value,
            "revision": entry.revision,
            "evidence_ids": list(entry.evidence_ids),
        }
        for entry in committed.ledger.entries
        if entry.fact_id is not None
        and (
            entry.fact_id not in before_ids
            or before_by_id[entry.fact_id].value != entry.after_value
        )
    ]
    character_state = [
        _fact_item(fact)
        for fact in committed.after.facts
        if fact.subject.startswith(_CHARACTER_SUBJECT_PREFIXES)
        and fact.epistemic_status == "confirmed_true"
    ]
    open_threads = [
        _fact_item(fact)
        for fact in committed.after.facts
        if fact.epistemic_status == "unknown"
    ]
    note = {
        "foreshadowing": [],
        "character_state": character_state,
        "open_threads": open_threads,
        "new_facts": new_facts,
        "found_contradictions": [],
        "arc_progress": [],
    }
    field_coverage = {
        "new_facts": "projected_from_wr",
        "character_state": "projected_from_wr",
        "open_threads": "projected_from_wr",
        "found_contradictions": "legacy_only_not_projected",
        "foreshadowing": "legacy_only_not_projected",
        "arc_progress": "legacy_only_not_projected",
    }
    source_change_types = {
        "new_facts": sorted(
            {entry.change_type for entry in committed.ledger.entries if entry.fact_id is not None}
        ),
        "character_state": ["character_fact", "employment_fact"],
        "open_threads": ["unknown_epistemic_fact"],
        "found_contradictions": [],
        "foreshadowing": [],
        "arc_progress": [],
    }
    return {
        "schema_version": HANDOVER_PROJECTION_VERSION,
        "commit_id": committed.commit_id,
        "note": note,
        "field_coverage": {
            field: {
                "status": field_coverage[field],
                "item_count": len(note[field]),
                "source_change_types": source_change_types[field],
            }
            for field in HANDOVER_FIELD_NAMES
        },
    }
