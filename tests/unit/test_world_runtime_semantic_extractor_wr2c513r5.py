import json

import pytest

from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c513r5 import (
    build_messages,
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


def _chained_state(gold, *, revision, clock):
    clock_fact = next(
        fact for fact in gold.state_before.facts
        if fact.subject == "world_clock" and fact.predicate == "time"
    )
    new_clock = clock_fact.model_copy(update={"value": clock})
    return gold.state_before.model_copy(update={
        "revision": revision,
        "facts": tuple(
            new_clock if fact.fact_id == clock_fact.fact_id else fact
            for fact in gold.state_before.facts
        ),
    })


def test_build_messages_uses_provided_state():
    gold = build_saturday_bakery_gold_fixture()
    chained = _chained_state(gold, revision=8, clock="04:40")
    messages = build_messages(text="正文。", state=chained)
    prompt = messages[1]["content"]
    assert '"04:40"' in prompt
    assert '"revision":8' in prompt


def test_build_messages_still_supports_state_variant():
    messages = build_messages(text="正文。", state_variant="before")
    prompt = messages[1]["content"]
    assert '"04:20"' in prompt


def test_build_messages_requires_state_or_variant():
    with pytest.raises(ValueError, match="state_or_state_variant"):
        build_messages(text="正文。")


def test_parse_uses_provided_state_for_before_values():
    gold = build_saturday_bakery_gold_fixture()
    chained = _chained_state(gold, revision=8, clock="04:40")
    text = "四点五十分，烤箱预热完成。"
    artifact = parse_semantic_response(
        text=text,
        response_text=json.dumps({"judgments": _all_false()}, ensure_ascii=False),
        sample_id="EXTRACTOR-R5-U-01",
        scene_id="extractor-r5",
        state=chained,
        base_revision=8,
    )
    clock = next(c for c in artifact.delta.changes if c.change_type == "clock_state")
    assert clock.before_value == "04:40"
    assert clock.after_value == "04:50"
    assert artifact.delta.base_revision == 8


def test_parse_defaults_to_before_fixture_without_state():
    text = "四点五十分，烤箱预热完成。"
    artifact = parse_semantic_response(
        text=text,
        response_text=json.dumps({"judgments": _all_false()}, ensure_ascii=False),
        sample_id="EXTRACTOR-R5-U-02",
        scene_id="extractor-r5",
    )
    clock = next(c for c in artifact.delta.changes if c.change_type == "clock_state")
    assert clock.before_value == "04:20"
    assert artifact.delta.base_revision == 7
