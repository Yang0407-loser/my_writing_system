import pytest

from app.character_arc_contract import (
    HARD_ARC_TRANSITION,
    OBSERVATIONAL_TEXTURE,
    ORDINARY_PLOT_EVENT,
    SOFT_ARC_PROGRESS,
)
from app.writing.character_arc_projection import (
    CharacterArcProjector,
    export_confirmed_v2_arcs,
    iter_projection_candidates,
)
from app.writing.outline_event_contract import OutlineEventContractCompiler


CHARACTERS = [
    {
        "id": "linwan",
        "name": "林晚",
        "personality": ["谨慎"],
        "motivation": "重新观察生活",
        "background": "离开广告行业",
    },
    {
        "id": "zhouye",
        "name": "周野",
        "personality": ["克制"],
        "motivation": "经营面包店",
        "background": "面包师",
    },
]


def _sub(key_points=None, event_contract=None):
    value = {
        "subsection": 1,
        "source_id": "sub-1",
        "title": "决定离开",
        "description": "",
        "key_points": key_points or [
            "林晚决定辞职",
            "林晚观察周野揉面",
            "林晚邀请周野合作，周野回应",
        ],
        "target_words": 1000,
    }
    if event_contract is not None:
        value["event_contract"] = event_contract
    return value


def _compile(sub=None):
    return OutlineEventContractCompiler().compile_chapter(
        section=1,
        subsections=[sub or _sub()],
        character_names=["林晚", "周野"],
        chapter_target_words=1000,
    )


def _confirmed_contract(sub=None):
    source = sub or _sub()
    compiler = OutlineEventContractCompiler()
    proposed = _compile(source).subsection_contracts[0].model_dump(mode="json")
    proposed["status"] = "confirmed"
    for event in proposed["events"]:
        event["status"] = "confirmed"
        event["user_confirmed"] = True
        event["requiredness"] = "soft"
    return compiler.confirm_submission(
        section=1,
        subsection=1,
        sub=source,
        submitted=proposed,
    )


def _replace_candidate(projection, replacement):
    return CharacterArcProjector().replace_confirmed_candidate(
        projection=projection,
        candidate=replacement,
    )


def test_projection_is_deterministic_and_has_stable_per_character_ids():
    projector = CharacterArcProjector()
    contract = _compile()
    first = projector.project(chapter_contract=contract, characters=CHARACTERS)
    second = projector.project(chapter_contract=contract, characters=CHARACTERS)
    assert first == second
    assert first.projection_hash == second.projection_hash
    ids = [candidate.projection_id for candidate in iter_projection_candidates(first)]
    assert len(ids) == len(set(ids))
    assert all(candidate.event_id in candidate.projection_id for candidate in iter_projection_candidates(first))


def test_unconfirmed_outline_events_never_become_authoritative_or_hard():
    projection = CharacterArcProjector().project(
        chapter_contract=_compile(),
        characters=CHARACTERS,
    )
    candidates = list(iter_projection_candidates(projection))
    assert candidates
    assert projection.authoritative_candidate_count == 0
    assert projection.hard_candidate_count == 0
    assert all(candidate.status == "proposed" for candidate in candidates)
    assert all(candidate.requiredness == "non_injectable" for candidate in candidates)
    assert all(not candidate.outline_event_authoritative for candidate in candidates)


def test_projection_classifies_fact_shape_without_claiming_arc_authority():
    projection = CharacterArcProjector().project(
        chapter_contract=_compile(),
        characters=CHARACTERS,
    )
    by_event_type = {}
    for candidate in iter_projection_candidates(projection):
        by_event_type.setdefault(candidate.event_type, set()).add(candidate.classification)
    assert by_event_type["decision"] == {SOFT_ARC_PROGRESS}
    assert by_event_type["observation"] == {OBSERVATIONAL_TEXTURE}
    assert by_event_type["dialogue_interaction"] == {ORDINARY_PLOT_EVENT}


def test_multi_actor_event_projects_once_per_exact_character_and_does_not_guess():
    projection = CharacterArcProjector().project(
        chapter_contract=_compile(),
        characters=CHARACTERS,
    )
    dialogue = [
        candidate for candidate in iter_projection_candidates(projection)
        if candidate.event_type == "dialogue_interaction"
    ]
    assert {candidate.character_id for candidate in dialogue} == {"linwan", "zhouye"}
    no_actor_contract = _compile(_sub(key_points=["有人决定离开"]))
    no_actor = CharacterArcProjector().project(
        chapter_contract=no_actor_contract,
        characters=CHARACTERS,
    )
    assert no_actor.candidate_count == 0
    assert no_actor.exclusions[0].reason == "no_exact_character_actor"


def test_projection_confirmation_requires_author_confirmed_outline_event():
    projector = CharacterArcProjector()
    candidate = next(iter_projection_candidates(projector.project(
        chapter_contract=_compile(),
        characters=CHARACTERS,
    )))
    with pytest.raises(ValueError, match="outline_event_not_confirmed"):
        projector.confirm_candidate(
            candidate=candidate,
            submitted={
                "projection_id": candidate.projection_id,
                "event_text_hash": candidate.event_text_hash,
                "classification": SOFT_ARC_PROGRESS,
            },
        )


def test_complete_hard_transition_requires_explicit_state_fields():
    projector = CharacterArcProjector()
    projection = projector.project(
        chapter_contract=_compile(_sub(
            event_contract=_confirmed_contract().model_dump(mode="json")
        )),
        characters=CHARACTERS,
    )
    candidate = next(
        item for item in iter_projection_candidates(projection)
        if item.character_id == "linwan" and item.event_type == "decision"
    )
    incomplete = projector.confirm_candidate(
        candidate=candidate,
        submitted={
            "projection_id": candidate.projection_id,
            "event_text_hash": candidate.event_text_hash,
            "classification": HARD_ARC_TRANSITION,
            "before_state": "犹豫",
            "after_state": "决定离开",
        },
    )
    assert incomplete.classification == SOFT_ARC_PROGRESS
    assert incomplete.requiredness == "soft"
    assert incomplete.missing_hard_fields

    complete = projector.confirm_candidate(
        candidate=candidate,
        submitted={
            "projection_id": candidate.projection_id,
            "event_text_hash": candidate.event_text_hash,
            "classification": HARD_ARC_TRANSITION,
            "before_state": "犹豫",
            "trigger": "收到驳回",
            "after_state": "决定离开",
            "observable_evidence": "发送辞职信",
            "rationale": "主动结束旧职业阶段",
        },
    )
    assert complete.classification == HARD_ARC_TRANSITION
    assert complete.requiredness == "hard"
    assert complete.status == "confirmed"
    assert complete.user_confirmed is True


def test_confirmed_projection_invalidates_on_event_source_change_and_removal():
    projector = CharacterArcProjector()
    confirmed_outline = _confirmed_contract()
    original_sub = _sub(event_contract=confirmed_outline.model_dump(mode="json"))
    original_projection = projector.project(
        chapter_contract=_compile(original_sub),
        characters=CHARACTERS,
    )
    candidate = next(
        item for item in iter_projection_candidates(original_projection)
        if item.character_id == "linwan" and item.event_type == "decision"
    )
    confirmed_candidate = projector.confirm_candidate(
        candidate=candidate,
        submitted={
            "projection_id": candidate.projection_id,
            "event_text_hash": candidate.event_text_hash,
            "classification": SOFT_ARC_PROGRESS,
            "rationale": "角色主动改变生活方向",
        },
    )
    prior = _replace_candidate(original_projection, confirmed_candidate)

    changed_sub = _sub(
        key_points=[
            "林晚决定暂缓辞职",
            "林晚观察周野揉面",
            "林晚邀请周野合作，周野回应",
        ],
        event_contract=confirmed_outline.model_dump(mode="json"),
    )
    changed = projector.project(
        chapter_contract=_compile(changed_sub),
        characters=CHARACTERS,
        prior_projection=prior.model_dump(mode="json"),
    )
    stale = next(
        item for item in iter_projection_candidates(changed)
        if item.projection_id == confirmed_candidate.projection_id
    )
    assert stale.status == "stale"
    assert stale.user_confirmed is False
    assert stale.requiredness == "non_injectable"

    removed_sub = _sub(
        key_points=[
            "林晚观察周野揉面",
            "林晚邀请周野合作，周野回应",
        ],
        event_contract=confirmed_outline.model_dump(mode="json"),
    )
    removed = projector.project(
        chapter_contract=_compile(removed_sub),
        characters=CHARACTERS,
        prior_projection=prior.model_dump(mode="json"),
    )
    superseded = next(
        item for item in iter_projection_candidates(removed)
        if item.projection_id == confirmed_candidate.projection_id
    )
    assert superseded.status == "superseded"
    assert superseded.invalidation_reason == "source_event_removed"


def test_only_explicit_confirmations_export_to_v2_and_no_edges_are_created():
    projector = CharacterArcProjector()
    confirmed_outline = _confirmed_contract()
    projection = projector.project(
        chapter_contract=_compile(_sub(
            event_contract=confirmed_outline.model_dump(mode="json")
        )),
        characters=CHARACTERS,
    )
    candidate = next(
        item for item in iter_projection_candidates(projection)
        if item.character_id == "linwan" and item.event_type == "decision"
    )
    confirmed = projector.confirm_candidate(
        candidate=candidate,
        submitted={
            "projection_id": candidate.projection_id,
            "event_text_hash": candidate.event_text_hash,
            "classification": SOFT_ARC_PROGRESS,
            "rationale": "允许改写的角色推进",
        },
    )
    updated = _replace_candidate(projection, confirmed)
    exported = export_confirmed_v2_arcs(updated)
    assert len(exported) == 1
    assert exported[0]["character_id"] == "linwan"
    assert exported[0]["key_milestones"][0]["milestone_id"] == confirmed.projection_id
    assert "depends_on" not in exported[0]["key_milestones"][0]
    assert "causes" not in exported[0]["key_milestones"][0]


def test_adding_subsection_preserves_existing_confirmed_projection_identity():
    compiler = OutlineEventContractCompiler()
    projector = CharacterArcProjector()
    confirmed_outline = _confirmed_contract()
    first_sub = _sub(event_contract=confirmed_outline.model_dump(mode="json"))
    first_chapter = compiler.compile_chapter(
        section=1,
        subsections=[first_sub],
        character_names=["林晚", "周野"],
        chapter_target_words=1000,
    )
    first_projection = projector.project(
        chapter_contract=first_chapter,
        characters=CHARACTERS,
    )
    candidate = next(
        item for item in iter_projection_candidates(first_projection)
        if item.character_id == "linwan" and item.event_type == "decision"
    )
    confirmed_candidate = projector.confirm_candidate(
        candidate=candidate,
        submitted={
            "projection_id": candidate.projection_id,
            "event_text_hash": candidate.event_text_hash,
            "classification": SOFT_ARC_PROGRESS,
            "rationale": "方向变化",
        },
    )
    prior = _replace_candidate(first_projection, confirmed_candidate)
    second_sub = {
        "subsection": 2,
        "source_id": "sub-2",
        "title": "后续",
        "description": "",
        "key_points": ["周野开始整理面包店"],
        "target_words": 800,
    }
    expanded_chapter = compiler.compile_chapter(
        section=1,
        subsections=[first_sub, second_sub],
        character_names=["林晚", "周野"],
        chapter_target_words=1800,
    )
    expanded = projector.project(
        chapter_contract=expanded_chapter,
        characters=CHARACTERS,
        prior_projection=prior.model_dump(mode="json"),
    )
    preserved = next(
        item for item in iter_projection_candidates(expanded)
        if item.projection_id == confirmed_candidate.projection_id
    )
    assert preserved.status == "confirmed"
    assert preserved.event_text_hash == confirmed_candidate.event_text_hash
    assert preserved.user_confirmed is True
