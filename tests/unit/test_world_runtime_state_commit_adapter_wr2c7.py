import json

import pytest

from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from experiments.world_runtime_writer_canary.delta_shadow_wr2c6 import validate_delta_v6
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c513r8 import (
    parse_semantic_response,
)
from experiments.world_runtime_writer_canary.state_commit_adapter_wr2c7 import (
    to_committable,
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
    facts = {}
    for fact in gold.state_before.facts:
        if fact.subject == "world_clock" and fact.predicate == "time":
            fact = fact.model_copy(update={"value": clock})
        facts[fact.fact_id] = fact
    return gold.state_before.model_copy(update={
        "revision": revision,
        "facts": tuple(sorted(facts.values(), key=lambda item: item.fact_id)),
    })


def _multi_clock_delta(gold, current):
    text = "六点零七分，周野看了一眼挂钟；六点二十二分，他又看了一眼。"
    artifact = parse_semantic_response(
        text=text,
        response_text=json.dumps({"judgments": _all_false()}, ensure_ascii=False),
        sample_id="ADAPTER-WR2C7-U-01",
        scene_id="adapter-wr2c7",
        state=current,
        base_revision=current.revision,
    )
    clocks = [c for c in artifact.delta.changes if c.change_type == "clock_state"]
    assert len(clocks) == 2
    assert [c.before_value for c in clocks] == ["06:00", "06:00"]
    validation = validate_delta_v6(artifact.delta, state=current)
    return artifact, validation


def test_multi_clock_same_delta_with_commit_start_before_is_accepted():
    gold = build_saturday_bakery_gold_fixture()
    current = _chained_state(gold, revision=9, clock="06:00")
    artifact, validation = _multi_clock_delta(gold, current)
    delta, committable_validation = to_committable(
        artifact.delta,
        validation,
        project_id=gold.state_before.project_id,
        base_state=gold.state_before,
        before_state=current,
    )
    assert [c.before_value for c in delta.changes] == ["06:00", "06:07"]
    assert [c.after_value for c in delta.changes] == ["06:07", "06:22"]
    committed = WorldRuntimeStateCommitter().commit(
        idempotency_key="adapter-wr2c7:multi-clock",
        before=current,
        delta=delta,
        validation=committable_validation,
        final_text_hash=artifact.output_hash,
    )
    clock = next(
        fact for fact in committed.after.facts
        if fact.subject == "world_clock" and fact.predicate == "time"
    )
    assert clock.value == "06:22"
    assert committed.after.revision == 10


def test_hallucinated_before_value_is_still_rejected():
    gold = build_saturday_bakery_gold_fixture()
    current = _chained_state(gold, revision=9, clock="06:00")
    artifact, validation = _multi_clock_delta(gold, current)
    target = artifact.delta.changes[1]
    broken = target.model_copy(update={"before_value": "never-was"})
    broken_delta = artifact.delta.model_copy(update={
        "changes": tuple(
            broken if change.change_id == target.change_id else change
            for change in artifact.delta.changes
        ),
    })
    with pytest.raises(ValueError, match="before value mismatch"):
        to_committable(
            broken_delta,
            validation,
            project_id=gold.state_before.project_id,
            base_state=gold.state_before,
            before_state=current,
        )
