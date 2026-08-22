import json

from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from experiments.world_runtime_writer_canary.delta_shadow_wr2c6 import validate_delta_v6
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


def _chained_open_state(gold, *, revision, clock):
    facts = {}
    for fact in gold.state_before.facts:
        if fact.subject == "world_clock" and fact.predicate == "time":
            fact = fact.model_copy(update={"value": clock})
        if fact.subject == "bakery:wild-bread:storefront" and fact.predicate == "operation_state":
            fact = fact.model_copy(update={"value": "open"})
        facts[fact.fact_id] = fact
    return gold.state_before.model_copy(update={
        "revision": revision,
        "facts": tuple(sorted(facts.values(), key=lambda item: item.fact_id)),
    })


def _sale_delta(gold, *, base_revision=7, state_variant="before"):
    text = "六点十分，顾客扫码买走一袋面包。"
    judgments = _all_false()
    judgments[_TYPES.index("clock_state")] = {
        "change_type": "clock_state",
        "occurred": True,
        "after_value": "06:10",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "六点十分", "occurrence": 1}],
    }
    judgments[_TYPES.index("storefront_public_sale")] = {
        "change_type": "storefront_public_sale",
        "occurred": True,
        "after_value": "occurred",
        "mode": "actual",
        "epistemic": "asserted",
        "evidence": [{"excerpt": "顾客扫码买走一袋面包", "occurrence": 1}],
    }
    return parse_semantic_response(
        text=text,
        response_text=json.dumps({"judgments": judgments}, ensure_ascii=False),
        sample_id="VALIDATOR-WR2C6-U-01",
        scene_id="validator-wr2c6",
        state_variant=state_variant,
        base_revision=base_revision,
    ).delta


def test_sale_is_invalid_against_closed_base_state():
    gold = build_saturday_bakery_gold_fixture()
    delta = _sale_delta(gold, base_revision=7)
    validation = validate_delta_v6(delta, state=gold.state_before)
    sale = next(
        item for item in validation.items
        if item.change_id.endswith(":2")
    )
    assert sale.outcome == "invalid"


def test_sale_is_valid_against_chained_open_state():
    gold = build_saturday_bakery_gold_fixture()
    delta = _sale_delta(gold, base_revision=8)
    chained = _chained_open_state(gold, revision=8, clock="06:00")
    validation = validate_delta_v6(delta, state=chained)
    sale = next(
        item for item in validation.items
        if item.change_id.endswith(":2")
    )
    assert sale.outcome == "valid"
    assert delta.base_revision == chained.revision


def test_validate_without_state_uses_wr1_fixture():
    gold = build_saturday_bakery_gold_fixture()
    delta = _sale_delta(gold, base_revision=7)
    validation = validate_delta_v6(delta)
    assert len(validation.items) == 2
    assert validation.base_revision == 7
