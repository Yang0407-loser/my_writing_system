from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_handover_projection import project_handover
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


def test_gold_handover_projection_populates_covered_fields():
    projection = project_handover(_gold_committed())
    note = projection["note"]
    assert len(note["new_facts"]) == 3
    assert len(note["character_state"]) == 3
    assert len(note["open_threads"]) == 2
    assert note["foreshadowing"] == []
    assert note["found_contradictions"] == []
    assert note["arc_progress"] == []


def test_field_coverage_marks_legacy_only_fields():
    projection = project_handover(_gold_committed())
    coverage = projection["field_coverage"]
    assert coverage["new_facts"]["status"] == "projected_from_wr"
    assert coverage["character_state"]["status"] == "projected_from_wr"
    assert coverage["open_threads"]["status"] == "projected_from_wr"
    assert coverage["foreshadowing"]["status"] == "legacy_only_not_projected"
    assert coverage["found_contradictions"]["status"] == "legacy_only_not_projected"
    assert coverage["arc_progress"]["status"] == "legacy_only_not_projected"


def test_handover_projection_is_deterministic():
    committed = _gold_committed()
    assert project_handover(committed) == project_handover(committed)
