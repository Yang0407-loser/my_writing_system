"""WR3.5 projection: WR commit -> legacy WorldState facts + RAG metadata.

Deterministic v1 projection of WR committed facts onto the legacy
``world_state.WorldFact`` dict shape and the retrieval metadata conventions
(``characters`` list is the primary retrieval field; time/locations/revision
are additive metadata that do not conflict with existing consumers).
"""

from __future__ import annotations

from typing import Any

from .world_runtime_legacy_projection import legacy_fact_mapping


METADATA_PROJECTION_VERSION = "world-runtime-metadata-projection-wr3.5-v1"

_CHARACTER_DISPLAY = {
    "character:lin-wan": "林晚",
    "character:zhou-ye": "周野",
    "character:ji-qing": "季晴",
    "character:coworker": "老吴",
}


def _display_name(subject: str) -> str:
    if subject.startswith("employment:"):
        subject = "character:" + subject.split(":", 1)[1]
    return _CHARACTER_DISPLAY.get(subject, subject.split(":", 1)[-1])


def project_rag_metadata(
    committed,
    *,
    section: int = 1,
    subsection: int = 1,
) -> dict[str, Any]:
    """Project one WR commit into retrieval metadata."""
    # employment: subjects are character-state facts (e.g. employment:lin-wan
    # -> 林晚); without this prefix, the primary retrieval field drops the
    # protagonist whose facts live on the employment subject.
    characters = sorted({
        _display_name(fact.subject)
        for fact in committed.after.facts
        if fact.subject.startswith(("character:", "employment:"))
    })
    clock = next(
        (
            fact for fact in committed.after.facts
            if fact.subject == "world_clock" and fact.predicate == "time"
        ),
        None,
    )
    weekday = next(
        (
            fact for fact in committed.after.facts
            if fact.subject == "world_clock" and fact.predicate == "weekday"
        ),
        None,
    )
    locations = sorted({
        str(fact.value)
        for fact in committed.after.facts
        if fact.subject.startswith("character:") and fact.predicate == "location"
    })
    return {
        "schema_version": METADATA_PROJECTION_VERSION,
        "commit_id": committed.commit_id,
        "metadata": {
            "characters": characters,
            "time": clock.value if clock is not None else None,
            "weekday": weekday.value if weekday is not None else None,
            "locations": locations,
            "world_revision": committed.after.revision,
            "source": "world_runtime_wr3.5",
            "section": section,
            "subsection": subsection,
        },
        "coverage": {
            "characters_status": "projected_from_wr",
            "time_status": "projected_from_wr" if clock is not None else "absent",
            "locations_status": "projected_from_wr" if locations else "absent",
        },
    }


def project_world_state_facts(
    committed,
    *,
    section: int = 1,
    subsection: int = 1,
) -> dict[str, Any]:
    """Project one WR commit into legacy world_state WorldFact dicts."""
    rows = []
    for fact in committed.after.facts:
        mapping = legacy_fact_mapping(fact.subject, fact.predicate)
        category = mapping[0] if mapping is not None else "world_fact"
        rows.append({
            "fact_id": f"wr:{fact.fact_id}",
            "category": category,
            "fact": f"{fact.subject} {fact.predicate} = {fact.value}",
            "source_section": section,
            "source_subsection": subsection,
            "immutable": False,
            "verified": fact.epistemic_status == "confirmed_true",
            "epistemic_status": fact.epistemic_status,
            "revision": fact.revision,
        })
    return {
        "schema_version": METADATA_PROJECTION_VERSION,
        "commit_id": committed.commit_id,
        "facts": rows,
        "count": len(rows),
    }
