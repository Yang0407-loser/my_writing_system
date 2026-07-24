from app.writing.outline_event_contract import (
    LegacyOutlineEventAdapter,
    OutlineEventContractCompiler,
    canonicalise_confirmed_tree,
)


NAMES = ["林晚", "周野"]


def _sub(**overrides):
    value = {
        "subsection": 1,
        "source_id": "sub-1",
        "title": "三个周六",
        "description": "",
        "key_points": [
            "第一个周六林晚听见周野揉面的声音",
            "第二个周六林晚拍下周野揉面的背影",
            "第三个周六周野递水，林晚回应并继续拍摄",
        ],
        "target_words": 1000,
    }
    value.update(overrides)
    return value


def _chapter(subsections):
    return OutlineEventContractCompiler().compile_chapter(
        section=1,
        subsections=subsections,
        character_names=NAMES,
        chapter_target_words=sum(s.get("target_words", 1000) for s in subsections),
    )


def _confirm(sub):
    compiler = OutlineEventContractCompiler()
    proposed = _chapter([sub]).subsection_contracts[0].model_dump(mode="json")
    proposed["status"] = "confirmed"
    for event in proposed["events"]:
        event["requiredness"] = "soft"
        event["status"] = "confirmed"
        event["user_confirmed"] = True
    proposed["stop_after_event_id"] = proposed["events"][-1]["event_id"]
    return compiler.confirm_submission(
        section=1, subsection=1, sub=sub, submitted=proposed
    )


def test_same_input_has_stable_ids_and_hash():
    first = _chapter([_sub()]).subsection_contracts[0]
    second = _chapter([_sub()]).subsection_contracts[0]
    assert [e.event_id for e in first.events] == [e.event_id for e in second.events]
    assert first.contract_hash == second.contract_hash


def test_legacy_authority_never_auto_promotes_hard_or_high():
    events = LegacyOutlineEventAdapter().extract(
        section=1,
        subsection=1,
        source_id="sub-1",
        description="",
        key_points=_sub()["key_points"],
        character_names=NAMES,
    )
    assert events
    assert all(event.requiredness == "unspecified" for event in events)
    assert all(event.status == "proposed" for event in events)
    assert all(not event.user_confirmed for event in events)
    assert all(event.confidence in {"low", "medium"} for event in events)


def test_description_exact_duplicate_is_not_counted_twice_but_new_action_is_kept():
    events = LegacyOutlineEventAdapter().extract(
        section=1,
        subsection=1,
        source_id="sub-1",
        description="林晚邀请周野进店并得到回应。随后林晚写下记录。",
        key_points=["林晚邀请周野进店并得到回应"],
        character_names=NAMES,
    )
    assert len(events) == 2
    assert [event.source_slot for event in events] == ["kp:001", "desc:002"]


def test_interaction_chain_and_mixed_time_are_preserved_without_actor_guessing():
    events = LegacyOutlineEventAdapter().extract(
        section=1,
        subsection=1,
        source_id="sub-1",
        description="",
        key_points=["林晚想起上周对周野的邀请，当前决定回应，并计划下周再见"],
        character_names=NAMES,
    )
    assert len(events) == 1
    assert events[0].unit_type == "dialogue_interaction"
    assert events[0].temporal_scope == "mixed"
    assert events[0].actors == ("林晚", "周野")


def test_next_subsection_events_are_deferred_and_stop_is_not_inferred():
    current = _sub()
    next_sub = _sub(
        subsection=2,
        source_id="sub-2",
        title="第一篇草稿",
        key_points=["林晚退出店外", "林晚在社区完成记录", "林晚形成初步反思"],
    )
    contracts = _chapter([current, next_sub]).subsection_contracts
    assert contracts[0].deferred_event_ids == tuple(
        event.event_id for event in contracts[1].events
    )
    assert contracts[0].stop_after_event_id is None
    assert all(event.subsection == 1 for event in contracts[0].events)


def test_confirmation_is_authoritative_but_source_change_makes_only_affected_event_stale():
    original = _sub()
    confirmed = _confirm(original)
    changed = _sub(key_points=[
        original["key_points"][0],
        "第二个周六林晚在店外记录周野的背影",
        original["key_points"][2],
    ])
    changed["event_contract"] = confirmed.model_dump(mode="json")
    updated = _chapter([changed]).subsection_contracts[0]
    assert updated.status == "stale"
    assert updated.events[0].status == "confirmed"
    assert updated.events[0].event_id == confirmed.events[0].event_id
    assert updated.events[1].status == "stale"
    assert updated.events[1].event_id == confirmed.events[1].event_id
    assert updated.events[1].user_confirmed is False
    assert updated.events[2].status == "confirmed"


def test_deletion_supersedes_and_insertion_does_not_renumber_confirmed_events():
    original = _sub()
    confirmed = _confirm(original)
    inserted = _sub(key_points=[
        "开店前林晚先检查相机",
        original["key_points"][0],
        original["key_points"][2],
    ])
    inserted["event_contract"] = confirmed.model_dump(mode="json")
    updated = _chapter([inserted]).subsection_contracts[0]
    by_text = {event.summary: event for event in updated.events}
    assert by_text[original["key_points"][0]].event_id == confirmed.events[0].event_id
    assert by_text[original["key_points"][2]].event_id == confirmed.events[2].event_id
    removed = [event for event in updated.events if event.status == "superseded"]
    assert len(removed) == 1
    assert removed[0].event_id == confirmed.events[1].event_id
    new_event = by_text["开店前林晚先检查相机"]
    assert new_event.status == "proposed"
    assert new_event.event_id not in {event.event_id for event in confirmed.events}

    submitted = updated.model_dump(mode="json")
    submitted["status"] = "confirmed"
    submitted["confirmation_requested"] = True
    for event in submitted["events"]:
        if event["status"] != "superseded":
            event["status"] = "confirmed"
            event["user_confirmed"] = True
            event["requiredness"] = "soft"
    reconfirmed = OutlineEventContractCompiler().confirm_submission(
        section=1,
        subsection=1,
        sub=inserted,
        submitted=submitted,
    )
    assert len({event.event_id for event in reconfirmed.events}) == len(
        reconfirmed.events
    )
    assert {
        event.event_id for event in reconfirmed.events
    } == {
        event.event_id for event in updated.events if event.status != "superseded"
    }


def test_new_subsection_does_not_change_existing_confirmed_event_hashes():
    original = _sub()
    confirmed = _confirm(original)
    stored = dict(original, event_contract=confirmed.model_dump(mode="json"))
    before = _chapter([stored]).subsection_contracts[0]
    after = _chapter([
        stored,
        _sub(subsection=2, source_id="sub-2", key_points=["林晚完成草稿"]),
    ]).subsection_contracts[0]
    assert [event.text_hash for event in before.events] == [
        event.text_hash for event in after.events
    ]
    assert [event.event_id for event in before.events] == [
        event.event_id for event in after.events
    ]


def test_confirmed_tree_uses_existing_save_shape_and_keeps_next_boundary():
    proposed = _chapter([_sub(), _sub(subsection=2, source_id="sub-2")])
    submitted = proposed.subsection_contracts[0].model_dump(mode="json")
    submitted["status"] = "confirmed"
    for event in submitted["events"]:
        event["status"] = "confirmed"
        event["user_confirmed"] = True
        event["requiredness"] = "soft"
    tree = [{
        "id": "section-1",
        "children": [
            dict(_sub(), id="sub-1", event_contract=submitted),
            dict(_sub(subsection=2, source_id="sub-2"), id="sub-2"),
        ],
    }]
    canonicalise_confirmed_tree(tree)
    stored = tree[0]["children"][0]["event_contract"]
    assert stored["status"] == "confirmed"
    assert stored["deferred_event_ids"]
