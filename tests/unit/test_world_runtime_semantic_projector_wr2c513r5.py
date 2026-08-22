import json

from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c513r5 import (
    parse_semantic_response,
)


_TYPES = (
    "storefront_public_sale", "storefront_public_handoff", "storefront_operation_state",
    "knowledge_state", "resignation_acknowledgement", "unsourced_project_fact",
    "object_state", "repeated_completed_event", "employment_state", "publication_state",
    "resignation_delivery", "resignation_personal_record", "clock_state", "location_state",
)


def _all_false():
    return [
        {
            "change_type": change_type,
            "occurred": False,
            "after_value": None,
            "mode": "actual",
            "epistemic": "asserted",
            "evidence": [],
        }
        for change_type in _TYPES
    ]


def _parse(text, items=None, state=None):
    return parse_semantic_response(
        text=text,
        response_text=json.dumps({"judgments": items if items is not None else _all_false()}, ensure_ascii=False),
        sample_id="PROJECTOR-R5-U-01",
        scene_id="projector-r5",
        state_variant="before",
        state=state,
        base_revision=7,
    )


def _clocks(artifact):
    return [
        change.after_value
        for change in artifact.delta.changes
        if change.change_type == "clock_state"
    ]


def test_bare_schedule_reference_is_not_projected():
    text = "面包房六点开门，还有不到一个半小时。"
    artifact = _parse(text)
    assert _clocks(artifact) == []


def test_schedule_reference_in_judgment_evidence_is_dropped():
    judgments = _all_false()
    judgments[_TYPES.index("clock_state")] = {
        "change_type": "clock_state",
        "occurred": True,
        "after_value": "06:00",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "面包房六点开门", "occurrence": 1}],
    }
    artifact = _parse("面包房六点开门。", judgments)
    assert _clocks(artifact) == []


def test_cha_fen_parses_to_before_hour():
    text = "六点差五分，林晚打开店门。"
    artifact = _parse(text)
    assert _clocks(artifact) == ["05:55"]


def test_cha_yike_parses_to_before_hour():
    text = "六点差一刻，林晚打开店门。"
    artifact = _parse(text)
    assert _clocks(artifact) == ["05:45"]


def test_guo_yike_parses_to_quarter_past():
    text = "七点过一刻，店里客人多起来。"
    artifact = _parse(text)
    assert _clocks(artifact) == ["07:15"]


def test_yike_and_ban_parse():
    gold = build_saturday_bakery_gold_fixture()
    clock_fact = next(
        fact for fact in gold.state_before.facts
        if fact.subject == "world_clock" and fact.predicate == "time"
    )
    new_clock = clock_fact.model_copy(update={"value": "02:00"})
    state = gold.state_before.model_copy(update={
        "facts": tuple(
            new_clock if fact.fact_id == clock_fact.fact_id else fact
            for fact in gold.state_before.facts
        ),
    })
    artifact = _parse("三点一刻，周野关火；六点半，烤箱预热完成。", state=state)
    assert _clocks(artifact) == ["03:15", "06:30"]


def test_past_day_reference_still_skipped_and_scene_time_kept():
    text = "面种是昨晚十点喂过的；四点五十分，烤箱预热完成。"
    artifact = _parse(text)
    assert _clocks(artifact) == ["04:50"]


def test_multi_clock_scene_times_still_work():
    text = "五点四十八分，林晚看了一眼手机上的时间；六点十分，她把第一盘贝果送进烤炉。"
    artifact = _parse(text)
    assert _clocks(artifact) == ["05:48", "06:10"]
