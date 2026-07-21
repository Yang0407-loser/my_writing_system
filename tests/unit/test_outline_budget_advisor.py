from app.writing.outline_budget_advisor import (
    OutlineBudgetAdvisor,
    allocate_largest_remainder,
)


def _sub(**overrides):
    value = {
        "source_id": "sub-1",
        "description": "",
        "key_points": ["林晚邀请周野一起整理面包店，周野答应并开始行动"],
        "target_words": 600,
    }
    value.update(overrides)
    return value


def test_key_points_are_stable_and_interaction_chain_stays_whole():
    advisor = OutlineBudgetAdvisor()
    first = advisor.extract_units(
        section=1,
        subsection=1,
        source_id="sub-1",
        description="同一段摘要不应额外算作事件。",
        key_points=_sub()["key_points"],
        character_names=["林晚", "周野"],
    )
    second = advisor.extract_units(
        section=1,
        subsection=1,
        source_id="sub-1",
        description="同一段摘要不应额外算作事件。",
        key_points=_sub()["key_points"],
        character_names=["林晚", "周野"],
    )
    assert first == second
    assert len(first) == 1
    assert first[0].unit_type == "interaction_chain"
    assert first[0].actors == ["林晚", "周野"]


def test_description_fallback_splits_action_chains_with_medium_confidence():
    units = OutlineBudgetAdvisor().extract_units(
        section=1,
        subsection=1,
        source_id="sub-1",
        description="林晚收到邮件并决定辞职。她回到家写下新的计划。随后开始记录社区生活。",
        key_points=[],
        character_names=["林晚"],
    )
    assert len(units) == 3
    assert {unit.confidence for unit in units} == {"medium"}
    assert all(unit.source_id.startswith("sub-1:description:") for unit in units)


def test_specific_time_anchors_are_not_double_counted():
    advice = OutlineBudgetAdvisor().advise_subsection(
        section=1,
        subsection=1,
        sub=_sub(key_points=[
            "第一个周六林晚只听见声音",
            "第二个周六林晚看见周野的背影",
            "第三个周六林晚和周野在面包店门口相遇",
        ]),
        style_profile={},
        character_names=["林晚", "周野"],
    )
    assert advice.time_jump_count == 2
    assert advice.actor_count == 2
    assert advice.scene_change_count == 0


def test_explicit_locations_and_exact_character_names_are_explainable():
    advice = OutlineBudgetAdvisor().advise_subsection(
        section=1,
        subsection=1,
        sub=_sub(key_points=[
            "林晚在面包店记录清晨来客",
            "周野回到书店回答顾言的问题",
        ]),
        style_profile={},
        character_names=["林晚", "周野", "顾言", "来客"],
    )
    assert advice.scene_change_count == 1
    assert advice.actor_count == 4
    assert "multi_actor_coordination" in advice.reason_codes


def test_only_four_current_style_knobs_affect_formula():
    advisor = OutlineBudgetAdvisor()
    base = advisor.advise_subsection(
        section=1, subsection=1, sub=_sub(), style_profile={},
        character_names=["林晚", "周野"],
    )
    rich = advisor.advise_subsection(
        section=1,
        subsection=1,
        sub=_sub(),
        style_profile={
            "sentence_preference": "long",
            "sensory_density": "rich",
            "dialogue_ratio": 0.5,
            "emotion_intensity": 80,
        },
        character_names=["林晚", "周野"],
    )
    legacy = advisor.advise_subsection(
        section=1,
        subsection=1,
        sub=_sub(),
        style_profile={"paragraph_length_avg": 900, "narrative_density": 1.0},
        character_names=["林晚", "周野"],
    )
    assert rich.style_factor == 1.15
    assert rich.emotion_intensity_observed == 80
    assert rich.recommended_preferred > base.recommended_preferred
    assert legacy.recommended_preferred == base.recommended_preferred


def test_paragraph_length_brief_is_conflict_only_not_budget_input():
    advisor = OutlineBudgetAdvisor()
    plain = advisor.advise_subsection(
        section=1, subsection=1, sub=_sub(), style_profile={},
        character_names=["林晚", "周野"], style_brief="",
    )
    conflict = advisor.advise_subsection(
        section=1, subsection=1, sub=_sub(), style_profile={},
        character_names=["林晚", "周野"], style_brief="每段保持在四百字左右。",
    )
    assert conflict.recommended_preferred == plain.recommended_preferred
    assert conflict.prompt_conflicts == [
        "paragraph_length_instruction_may_conflict_with_subsection_total"
    ]


def test_recommendation_bounds_and_result_are_deterministic():
    advisor = OutlineBudgetAdvisor()
    kwargs = dict(
        outline=[{"section": 1, "subsections": [_sub()]}],
        style_profile={"sentence_preference": "short"},
        character_names=["林晚", "周野"],
        chapter_budget=800,
    )
    first = advisor.advise_outline(**kwargs)
    second = advisor.advise_outline(**kwargs)
    advice = first.chapters[0].subsections[0]
    assert first == second
    assert advice.recommended_min <= advice.recommended_preferred <= advice.recommended_max


def test_target_above_range_requests_review_instead_of_claiming_keep():
    advice = OutlineBudgetAdvisor().advise_subsection(
        section=1,
        subsection=1,
        sub=_sub(target_words=5000),
        style_profile={},
        character_names=["林晚", "周野"],
    )
    assert advice.recommended_action == "review_structure"
    assert "current_target_above_recommended_max" in advice.reason_codes


def test_largest_remainder_allocation_conserves_total():
    allocation = allocate_largest_remainder(1000, [1, 1, 1])
    assert allocation == [334, 333, 333]
    assert sum(allocation) == 1000


def test_overconstrained_chapter_recommends_scope_reduction():
    result = OutlineBudgetAdvisor().advise_outline(
        outline=[{"section": 1, "subsections": [
            _sub(source_id="a", target_words=200),
            _sub(source_id="b", target_words=200),
        ]}],
        character_names=["林晚", "周野"],
        chapter_budget=300,
    )
    chapter = result.chapters[0]
    assert chapter.chapter_overconstrained is True
    assert chapter.allocated_total == 300
    assert all(item.recommended_action == "reduce_scope" for item in chapter.subsections)
