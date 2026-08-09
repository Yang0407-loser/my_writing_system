from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_character_projection import project_characters
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


def test_gold_character_projection_populates_character_attributes():
    projection = project_characters(_gold_committed())
    assert projection["coverage"]["character_count"] == 3
    assert projection["coverage"]["relation_count"] == 0
    assert projection["coverage"]["faction_count"] == 0
    characters = {row["character_id"]: row["attributes"] for row in projection["characters"]}
    assert "character:lin-wan" in characters
    assert "character:ji-qing" in characters
    assert "character:zhou-ye" in characters
    lin_wan = characters["character:lin-wan"]
    assert any(attr["predicate"] == "status" and attr["value"] == "employed" for attr in lin_wan)
    zhou_ye = characters["character:zhou-ye"]
    assert any(attr["predicate"] == "location" for attr in zhou_ye)


def test_relations_and_factions_are_explicitly_legacy_only():
    projection = project_characters(_gold_committed())
    coverage = projection["coverage"]
    assert coverage["relations_status"] == "legacy_only_not_projected"
    assert coverage["factions_status"] == "legacy_only_not_projected"
    assert all(
        status == "legacy_only_not_projected"
        for status in coverage["relation_store_fields"].values()
    )
    assert coverage["character_store_fields"]["name"] == "covered_from_character_id"


def test_character_projection_is_deterministic():
    committed = _gold_committed()
    assert project_characters(committed) == project_characters(committed)
