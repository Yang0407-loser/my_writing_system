from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_legacy_projection import (
    legacy_fact_mapping,
    project_world_state,
)
from app.writing.world_runtime_state_committer import (
    EventLedger,
    EventLedgerEntry,
)


def _gold():
    return build_saturday_bakery_gold_fixture()


def test_gold_state_projects_all_sixteen_facts():
    gold = _gold()
    projection = project_world_state(gold.state_after)
    assert projection["coverage"]["fact_count"] == 16
    assert projection["coverage"]["mapped_count"] == 16
    assert projection["coverage"]["unmapped_count"] == 0
    assert projection["coverage"]["event_only_entry_count"] == 0
    assert projection["coverage"]["exact_mapping_count"] == 8
    assert projection["coverage"]["approximate_mapping_count"] == 8
    fact_ids = [fact["fact_id"] for fact in projection["facts"]]
    assert len(fact_ids) == len(set(fact_ids))
    assert all(fact_id.startswith("legacy:") for fact_id in fact_ids)


def test_unknown_facts_project_with_unknown_status():
    gold = _gold()
    projection = project_world_state(gold.state_before)
    acknowledgement = next(
        fact for fact in projection["facts"]
        if fact["predicate"] == "resignation_acknowledged"
    )
    assert acknowledgement["status"] == "unknown"
    assert acknowledgement["confidence"] == 0.0
    assert acknowledgement["durability"] == "subsection"


def test_event_only_ledger_entries_are_listed_not_projected():
    gold = _gold()
    base = project_world_state(gold.state_after)
    before = len(base["facts"])
    ledger = EventLedger(
        ledger_id="ledger:test",
        project_id=gold.state_before.project_id,
        revision=8,
        entries=(
            EventLedgerEntry(
                ledger_id="ledger:test:1",
                revision=8,
                change_id="change:test:1",
                change_type="storefront_public_sale",
                subject="bakery:wild-bread:storefront",
                predicate="public_sale_event",
                after_value="occurred",
                evidence_ids=("ev:test",),
                output_hash="0" * 64,
                idempotency_key="test",
                fact_id=None,
            ),
        ),
    )
    projection = project_world_state(gold.state_after, ledger=ledger)
    assert len(projection["facts"]) == before
    assert projection["coverage"]["event_only_entry_count"] == 1
    assert projection["event_only_entries"][0]["change_type"] == "storefront_public_sale"


def test_projection_is_deterministic():
    gold = _gold()
    first = project_world_state(gold.state_after)
    second = project_world_state(gold.state_after)
    assert first["state_hash"] == second["state_hash"]
    assert first["facts"] == second["facts"]


def test_mapping_table_has_no_unknown_legacy_types():
    from app.writing.state_frame_v1 import FactType

    allowed = set(FactType.__args__)
    subjects = [
        "world_clock", "bakery:wild-bread:storefront", "bakery:wild-bread:workshop",
        "bakery:wild-bread", "character:lin-wan", "employment:lin-wan",
        "article:lin-wan", "company:lin-wan", "resignation:lin-wan", "object:bowl",
    ]
    predicates = [
        "time", "weekday", "operation_state", "access_state", "light",
        "open_days", "opens_at", "production_starts_at", "location",
        "article_knowledge", "status", "publication_state", "public_comment_count",
        "resignation_acknowledged", "lifecycle_state", "content_state",
        "temperature_state", "location_state",
    ]
    for subject in subjects:
        for predicate in predicates:
            mapping = legacy_fact_mapping(subject, predicate)
            if mapping is not None:
                assert mapping[0] in allowed, (subject, predicate, mapping)
