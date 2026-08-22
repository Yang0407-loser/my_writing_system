from app.writing.world_runtime_contracts import CanonicalWorldState
from app.writing.world_runtime_reviewer_projection import project_reviewer_context
from app.writing.world_runtime_relationship_projection import (
    project_relationships,
    render_relation_context,
)
from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from experiments.world_runtime_writer_canary.delta_shadow_wr2a import EvidenceSpan
from experiments.world_runtime_writer_canary.delta_shadow_wr3r1 import (
    ProposedChangeV3R1,
    ProposedTypedDeltaV3R1,
    validate_delta_v3r1,
)
from experiments.world_runtime_writer_canary.wr3r1_relationship_gold import (
    FINAL_TEXT,
    build_relationship_gold_fixture,
    relationship_committable,
)


def _state():
    return CanonicalWorldState(
        project_id="gold-project:saturday-bakery",
        revision=7,
        facts=(),
    )


def _change(
    change_id,
    sequence,
    subject,
    predicate,
    after_value,
    *,
    change_type="relationship_state",
    evidence_ids=("ev:test",),
):
    return ProposedChangeV3R1(
        change_id=change_id,
        sequence=sequence,
        change_type=change_type,
        subject=subject,
        predicate=predicate,
        before_value=None,
        before_epistemic_status="unknown",
        after_value=after_value,
        actor="narrator",
        mechanism="relationship_revealed",
        evidence_ids=evidence_ids,
    )


def _delta(changes, output_hash="0" * 64):
    return ProposedTypedDeltaV3R1(
        delta_id="delta:wr3r1-test",
        sample_id="WR3R1-TEST",
        scene_id="scene:test",
        project_id="gold-project:saturday-bakery",
        state_variant="before",
        base_revision=7,
        output_hash=output_hash,
        evidence=(
            EvidenceSpan(
                evidence_id="ev:test",
                claim="relationship reveal",
                start=0,
                end=10,
                excerpt=FINAL_TEXT[:10],
            ),
        ),
        changes=changes,
    )


def test_relationship_gold_closed_chain_audits():
    gold = build_relationship_gold_fixture()
    assert gold.state_before.revision == 7
    assert gold.state_after.revision == 8
    assert len(gold.committed_delta.changes) == 10
    assert len(gold.validation_result.rejected_change_ids) == 1
    assert gold.output_hash == gold.committed_delta.output_hash
    # self-referencing relationship is the rejected change
    rejected = gold.validation_result.rejected_change_ids[0]
    assert "self-invalid" in rejected


def test_relationship_gold_commits_and_projects():
    gold = build_relationship_gold_fixture()
    delta, validation = relationship_committable(gold)
    committed = WorldRuntimeStateCommitter().commit(
        idempotency_key="wr3r1:gold:test",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )
    assert committed.after.revision == 8
    relationship_facts = [
        fact for fact in committed.after.facts
        if fact.subject.startswith("relationship:")
    ]
    assert len(relationship_facts) == 10

    projection = project_relationships(committed)
    assert projection["coverage"]["status"] == "projected_from_wr"
    assert projection["coverage"]["relationship_count"] == 2
    text = render_relation_context(projection["relations"])
    assert "青梅竹马" in text
    assert "大学闺蜜" in text
    assert "羁绊 7/10" in text

    reviewer = project_reviewer_context(committed)
    assert "青梅竹马" in reviewer["relation_context"]
    assert reviewer["coverage"]["relation_context_status"] == "projected_from_wr"


def test_wr3r1_validator_accepts_valid_relationships():
    changes = (
        _change("rel-1", 1, "relationship:lin-wan:zhou-ye", "relation_type", "青梅竹马"),
        _change("rel-2", 2, "relationship:lin-wan:zhou-ye", "direction", "complex"),
        _change("rel-3", 3, "relationship:lin-wan:zhou-ye", "intensity", 7),
        _change("rel-4", 4, "relationship:ji-qing:lin-wan", "relation_type", "大学闺蜜"),
    )
    result = validate_delta_v3r1(_delta(changes), state=_state())
    assert set(result.accepted_change_ids) == {"rel-1", "rel-2", "rel-3", "rel-4"}
    assert result.rejected_change_ids == ()


def test_wr3r1_validator_rejects_invalid_relationships():
    changes = (
        _change("rel-self", 1, "relationship:lin-wan:lin-wan", "relation_type", "自己"),
        _change("rel-dir", 2, "relationship:lin-wan:zhou-ye", "direction", "diagonal"),
        _change("rel-int", 3, "relationship:lin-wan:zhou-ye", "intensity", 11),
        _change("rel-pred", 4, "relationship:lin-wan:zhou-ye", "unknown_predicate", "x"),
    )
    result = validate_delta_v3r1(_delta(changes), state=_state())
    assert result.accepted_change_ids == ()
    assert len(result.rejected_change_ids) == 4
    rules = {
        rule
        for item in result.items
        for rule in item.rule_ids
    }
    assert "kernel.relationship.subject_shape" in rules
    assert "kernel.relationship.direction_enum" in rules
    assert "kernel.relationship.intensity_range" in rules
    assert "kernel.relationship.predicate_unknown" in rules


def test_wr3r1_validator_delegates_non_relationship_types():
    changes = (
        _change(
            "rel-1",
            1,
            "relationship:lin-wan:zhou-ye",
            "relation_type",
            "青梅竹马",
        ),
        _change(
            "unsourced-1",
            2,
            "project:lin-wan",
            "life_goal",
            "记录100个生活切片",
            change_type="unsourced_project_fact",
        ),
    )
    result = validate_delta_v3r1(_delta(changes), state=_state())
    assert "rel-1" in result.accepted_change_ids
    assert "unsourced-1" in result.unresolved_change_ids
