"""WR3.4 projection: WR commit -> legacy character/relation/faction store shapes.

Deterministic v1 projection of WR committed facts onto the legacy
character / character_relation / faction store schemas.  Character attributes
with a WR ontology source are populated; relation and faction stores have no
WR equivalent yet and are explicitly marked ``legacy_only_not_projected`` so
the downstream migration gap stays visible.
"""

from __future__ import annotations

from typing import Any


CHARACTER_PROJECTION_VERSION = "world-runtime-character-projection-wr3.4-v1"

_CHARACTER_SUBJECT_PREFIXES = ("character:", "employment:")


def _character_id(subject: str) -> str:
    if subject.startswith("employment:"):
        return "character:" + subject.split(":", 1)[1]
    return subject


def project_characters(committed) -> dict[str, Any]:
    """Project one WR commit into legacy character/relation/faction shapes."""
    characters: dict[str, list[dict[str, Any]]] = {}
    for fact in committed.after.facts:
        if fact.epistemic_status != "confirmed_true":
            continue
        if not fact.subject.startswith(_CHARACTER_SUBJECT_PREFIXES):
            continue
        character_id = _character_id(fact.subject)
        characters.setdefault(character_id, []).append({
            "predicate": fact.predicate,
            "value": fact.value,
            "revision": fact.revision,
        })
    character_rows = [
        {"character_id": character_id, "attributes": attributes}
        for character_id, attributes in sorted(characters.items())
    ]
    return {
        "schema_version": CHARACTER_PROJECTION_VERSION,
        "commit_id": committed.commit_id,
        "characters": character_rows,
        "relations": [],
        "factions": [],
        "coverage": {
            "character_count": len(character_rows),
            "relation_count": 0,
            "faction_count": 0,
            "relations_status": "legacy_only_not_projected",
            "factions_status": "legacy_only_not_projected",
            "character_store_fields": {
                "name": "covered_from_character_id",
                "personality": "legacy_only_not_projected",
                "strengths": "legacy_only_not_projected",
                "weaknesses": "legacy_only_not_projected",
                "key_lines": "legacy_only_not_projected",
                "relationships": "legacy_only_not_projected",
                "custom": "legacy_only_not_projected",
            },
            "relation_store_fields": {
                field: "legacy_only_not_projected"
                for field in (
                    "relation_type", "direction", "intensity",
                    "stages", "current_stage", "description",
                )
            },
            "faction_store_fields": {
                field: "legacy_only_not_projected"
                for field in ("name", "type", "leader_name", "goal", "territory", "members")
            },
        },
    }
