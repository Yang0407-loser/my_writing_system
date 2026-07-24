from pathlib import Path

from app.character_arc_contract import (
    SOFT_ARC_PROGRESS,
    build_v2_edge_plan,
    normalize_v2_arcs,
)
from app.config import settings
from app.writing.character_arc_projection import (
    CharacterArcProjector,
    export_confirmed_v2_arcs,
    iter_projection_candidates,
)
from app.writing.outline_event_contract import OutlineEventContractCompiler


ROOT = Path(__file__).resolve().parents[2]


def _confirmed_projection():
    characters = [{"id": "c1", "name": "甲", "motivation": "离开旧生活"}]
    sub = {
        "subsection": 1,
        "source_id": "sub-1",
        "title": "决定",
        "description": "",
        "key_points": ["甲决定辞职"],
        "target_words": 500,
    }
    compiler = OutlineEventContractCompiler()
    proposed = compiler.compile_chapter(
        section=1,
        subsections=[sub],
        character_names=["甲"],
        chapter_target_words=500,
    ).subsection_contracts[0].model_dump(mode="json")
    proposed["status"] = "confirmed"
    for event in proposed["events"]:
        event["status"] = "confirmed"
        event["user_confirmed"] = True
        event["requiredness"] = "soft"
    confirmed_contract = compiler.confirm_submission(
        section=1,
        subsection=1,
        sub=sub,
        submitted=proposed,
    )
    chapter = compiler.compile_chapter(
        section=1,
        subsections=[dict(
            sub, event_contract=confirmed_contract.model_dump(mode="json")
        )],
        character_names=["甲"],
        chapter_target_words=500,
    )
    projector = CharacterArcProjector()
    projection = projector.project(
        chapter_contract=chapter,
        characters=characters,
    )
    candidate = next(iter_projection_candidates(projection))
    confirmed = projector.confirm_candidate(
        candidate=candidate,
        submitted={
            "projection_id": candidate.projection_id,
            "event_text_hash": candidate.event_text_hash,
            "classification": SOFT_ARC_PROGRESS,
            "rationale": "允许改写的推进",
        },
    )
    return projector.replace_confirmed_candidate(
        projection=projection,
        candidate=confirmed,
    ), chapter


def test_confirmed_export_is_v2_compatible_but_creates_no_implicit_edges():
    projection, chapter = _confirmed_projection()
    exported = export_confirmed_v2_arcs(projection)
    outline = [{
        "section": 1,
        "subsections": [{
            "subsection": 1,
            "title": chapter.subsection_contracts[0].title,
            "description": chapter.subsection_contracts[0].objective,
            "key_points": [],
        }],
    }]
    normalized = normalize_v2_arcs(exported, outline)
    assert normalized[0]["key_milestones"][0]["classification"] == SOFT_ARC_PROGRESS
    assert build_v2_edge_plan(normalized) == []


def test_projection_is_not_connected_to_production_arc_or_writer_modules():
    for relative in [
        "app/coordinator.py",
        "app/agents/writer.py",
        "app/agents/character_manager.py",
        "app/narrative_event.py",
    ]:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "character_arc_projection" not in source
    assert settings.CHARACTER_ARC_CONTRACT_VERSION == "v1"
