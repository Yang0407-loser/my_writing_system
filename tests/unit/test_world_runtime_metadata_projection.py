from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_metadata_projection import (
    project_rag_metadata,
    project_world_state_facts,
)
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


def test_rag_metadata_projects_characters_time_and_locations():
    projection = project_rag_metadata(_gold_committed())
    metadata = projection["metadata"]
    assert set(metadata["characters"]) == {"林晚", "周野", "季晴", "老吴"}
    assert metadata["time"] == "04:20"
    assert metadata["weekday"] == "saturday"
    assert "bakery:wild-bread:workshop" in metadata["locations"]
    assert metadata["world_revision"] == 8
    assert projection["coverage"]["characters_status"] == "projected_from_wr"


def test_world_state_facts_project_all_confirmed_facts():
    projection = project_world_state_facts(_gold_committed())
    assert projection["count"] == 16
    verified = {row["fact_id"] for row in projection["facts"] if row["verified"]}
    unknown = {row["fact_id"] for row in projection["facts"] if not row["verified"]}
    assert len(verified) == 14
    assert len(unknown) == 2
    categories = {row["category"] for row in projection["facts"]}
    assert "temporal_state" in categories
    assert "continuity_state" in categories
    publication = next(
        row for row in projection["facts"]
        if "publication_state" in row["fact"]
    )
    assert publication["category"] == "continuity_state"


def test_metadata_projection_is_deterministic():
    committed = _gold_committed()
    assert project_rag_metadata(committed) == project_rag_metadata(committed)
    assert project_world_state_facts(committed) == project_world_state_facts(committed)
