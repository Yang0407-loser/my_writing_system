from app.writing.narrative_reality_checks import NarrativeRealityChecker


def _codes(record):
    return {item["code"] for item in record["warnings"]}


def test_warns_when_activity_precedes_established_schedule():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "第三个周六，凌晨三点。门里已经传来揉面的声音。",
        section=1,
        subsection=1,
        known_context="野面包凌晨三点半开始揉面。",
    )

    assert "activity_before_established_schedule" in _codes(record)
    assert record["production_effect"] is False


def test_does_not_confuse_later_timestamp_with_earlier_activity():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "凌晨三点，她到了店门口。等到凌晨三点半，门里传来揉面的声音。",
        section=1,
        subsection=1,
        known_context="野面包凌晨三点半开始揉面。",
    )

    assert "activity_before_established_schedule" not in _codes(record)


def test_texture_quantity_is_not_parsed_as_clock_time():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "面团的香气留下一点微咸的发酵味，门里的揉面声还在继续。",
        section=1,
        subsection=1,
        known_context="凌晨三点半开始揉面。",
    )

    assert "activity_before_established_schedule" not in _codes(record)


def test_colloquial_clock_minutes_still_trigger_schedule_check():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "第三个周六，三点二十，门里已经传来揉面的声音。",
        section=1,
        subsection=1,
        known_context="凌晨三点半开始揉面。",
    )

    assert "activity_before_established_schedule" in _codes(record)


def test_warns_about_closed_day_activity_without_cause():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "今天是周三，店不营业。新鲜面包的香气却从门缝里钻出来。",
        section=1,
        subsection=1,
    )

    assert "closed_business_activity_without_cause" in _codes(record)


def test_closed_day_activity_with_stated_cause_is_allowed():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "今天是周三，店不营业。店主因为周末订单提前备货，香气从门缝里钻出来。",
        section=1,
        subsection=1,
    )

    assert "closed_business_activity_without_cause" not in _codes(record)


def test_cross_sentence_business_day_rule_detects_fresh_smell():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "今天是周三。店门关着，但她闻到了新鲜的、带着温度的香气。",
        section=1,
        subsection=1,
        known_context="野面包只在周六营业。",
    )

    assert "closed_business_activity_without_cause" in _codes(record)


def test_remembered_smell_on_closed_day_is_not_current_activity():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "今天是周三，店门关着。她想起周六闻到的新鲜香气。",
        section=1,
        subsection=1,
        known_context="野面包只在周六营业。",
    )

    assert "closed_business_activity_without_cause" not in _codes(record)


def test_closed_weekday_from_history_does_not_contaminate_later_saturday():
    checker = NarrativeRealityChecker()
    checker.observe(
        "今天是周三，店门关着。她只记得以前闻到的新鲜香气。",
        section=1,
        subsection=1,
        known_context="野面包只在周六营业。",
    )

    record = checker.observe(
        "第四个周六，面团的香气从门缝里飘出来。",
        section=1,
        subsection=2,
        known_context="野面包只在周六营业。",
    )

    assert "closed_business_activity_without_cause" not in _codes(record)


def test_warns_when_venue_has_two_separated_location_anchors():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "她走出国贸的写字楼，一股香气从街对面飘来。"
        "三天前，她在租住的老小区门口闻到过；那家店叫「野面包」。"
        "她打了车回家，窗外从国贸的高楼变成老小区的红砖楼。",
        section=1,
        subsection=1,
    )

    assert "location_anchor_conflict" in _codes(record)


def test_single_location_anchor_does_not_warn():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "她打了车离开国贸，回到老小区。小区门口那家店叫「野面包」。",
        section=1,
        subsection=1,
    )

    assert "location_anchor_conflict" not in _codes(record)


def test_warns_about_unsupported_named_entity():
    checker = NarrativeRealityChecker(allowed_names=["林晚", "周野"])

    record = checker.observe(
        "那个名字——程明——她轻轻念了一遍。",
        section=1,
        subsection=1,
    )

    assert "unsupported_named_entity" in _codes(record)


def test_name_from_authoritative_context_is_allowed():
    checker = NarrativeRealityChecker(allowed_names=["林晚"])

    record = checker.observe(
        "那个名字——程明——她轻轻念了一遍。",
        section=1,
        subsection=1,
        known_context="程明是面包店上一任店主。",
    )

    assert "unsupported_named_entity" not in _codes(record)


def test_warns_about_recording_in_private_workspace_without_permission():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "她跨过门槛走进操作间，举起相机，对准周野按下快门。",
        section=1,
        subsection=1,
    )

    assert "recording_without_explicit_permission" in _codes(record)


def test_recording_warning_skips_window_photo_and_points_to_person_photo():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "她举起相机，取景框里只有百叶窗。她按下快门，全是窗。"
        "四点半，操作间侧面的窗开了。她再次举起相机，"
        "取景框里那个背影转向案板，她按下快门。",
        section=1,
        subsection=1,
    )

    warning = next(
        item for item in record["warnings"]
        if item["code"] == "recording_without_explicit_permission"
    )
    assert "那个背影" in warning["evidence"]


def test_explicit_recording_permission_is_allowed():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "她走进操作间。周野说：“可以拍，但别拍正脸。”她举起相机按下快门。",
        section=1,
        subsection=1,
    )

    assert "recording_without_explicit_permission" not in _codes(record)


def test_warns_when_private_draft_is_later_treated_as_completed_resignation():
    checker = NarrativeRealityChecker()
    checker.observe(
        "她写了辞职信，收件人填自己的私人邮箱，然后点击发送。",
        section=1,
        subsection=1,
    )

    record = checker.observe(
        "辞职后，她第一次睡到自然醒。",
        section=1,
        subsection=2,
    )

    assert "institutional_action_marked_complete_without_delivery" in _codes(record)


def test_warns_when_friend_knows_resignation_without_transmission_path():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "她把辞职信发到自己的私人邮箱。回家后，季晴的消息：「你辞职了？」",
        section=1,
        subsection=1,
    )

    assert "knowledge_without_transmission_path" in _codes(record)


def test_explicit_information_transmission_is_allowed():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "她把辞职信发到自己的私人邮箱，又告诉季晴自己准备离职。"
        "回家后，季晴的消息：「你辞职了？」",
        section=1,
        subsection=1,
    )

    assert "knowledge_without_transmission_path" not in _codes(record)


def test_warns_when_bread_finishes_too_soon_after_kneading_start():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "凌晨三点四十分，周野正把一炉面包从烤箱里端出来。",
        section=1,
        subsection=1,
        known_context="野面包凌晨三点半开始揉面。",
    )

    assert "process_duration_without_prior_batch" in _codes(record)


def test_prepared_prior_batch_explains_short_process_window():
    checker = NarrativeRealityChecker()

    record = checker.observe(
        "凌晨三点四十分，周野把前一晚冷藏发酵的一炉面包从烤箱里端出来。",
        section=1,
        subsection=1,
        known_context="野面包凌晨三点半开始揉面。",
    )

    assert "process_duration_without_prior_batch" not in _codes(record)


def test_warns_when_same_cup_reappears_after_being_taken_home():
    checker = NarrativeRealityChecker()
    checker.observe(
        "她手里端着搪瓷杯，沿着路灯往家走。",
        section=1,
        subsection=1,
    )

    record = checker.observe(
        "周野站在门口，手里还是那只搪瓷杯。",
        section=1,
        subsection=2,
    )

    assert "object_location_conflict" in _codes(record)


def test_disabled_checker_has_no_observation_or_state():
    checker = NarrativeRealityChecker(enabled=False)

    record = checker.observe(
        "那个名字——程明——她轻轻念了一遍。",
        section=1,
        subsection=1,
    )

    assert record is None
    assert checker.records == []
