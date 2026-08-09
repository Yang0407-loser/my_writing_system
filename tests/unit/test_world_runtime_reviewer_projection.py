import json

from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_reviewer_projection import project_reviewer_context
from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from experiments.world_runtime_writer_canary import wr3_shadow_audit as audit


def _gold_committed():
    gold = build_saturday_bakery_gold_fixture()
    delta, validation = audit._gold_committable(gold)
    return WorldRuntimeStateCommitter().commit(
        idempotency_key="wr3:gold",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )


def test_reviewer_context_projects_wr_sources():
    projection = project_reviewer_context(_gold_committed())
    assert "character:lin-wan" in projection["character_consistency_context"]
    note = json.loads(projection["handover_chain"])
    assert note["new_facts"]
    assert projection["world_review_summary"]["revision"] == 8
    assert projection["world_review_summary"]["fact_count"] == 16
    assert projection["coverage"]["handover_chain_status"] == "projected_from_wr"


def test_relation_and_subplot_contexts_are_legacy_only():
    projection = project_reviewer_context(_gold_committed())
    assert "legacy_only" in projection["relation_context"]
    assert "legacy_only" in projection["subplot_context"]
    assert projection["coverage"]["relation_context_status"] == "legacy_only_not_projected"
    assert projection["coverage"]["subplot_context_status"] == "legacy_only_not_projected"


def test_reviewer_projection_is_deterministic():
    committed = _gold_committed()
    assert project_reviewer_context(committed) == project_reviewer_context(committed)
