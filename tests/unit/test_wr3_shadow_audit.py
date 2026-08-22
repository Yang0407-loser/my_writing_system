import pytest

from experiments.world_runtime_writer_canary import wr3_shadow_audit as audit


def test_legacy_coverage_projection_counts_covered_and_gaps():
    facts = [
        {"fact_id": "fact:clock:time", "subject": "world_clock", "predicate": "time", "value": "05:00"},
        {"fact_id": "fact:zhouye:location", "subject": "character:zhou-ye", "predicate": "location", "value": "bakery:wild-bread:workshop"},
        {"fact_id": "fact:article:status", "subject": "article:lin-wan", "predicate": "publication_state", "value": "published"},
    ]
    result = audit.legacy_coverage_projection(facts)
    assert result["fact_count"] == 3
    assert result["covered_count"] == 3
    assert result["no_legacy_equivalent_count"] == 0
    assert result["exact_mapping_count"] == 2
    assert result["approximate_mapping_count"] == 1
    covered = [row for row in result["rows"] if row["mapping_status"] == "covered"]
    assert ("temporal_state", "time") in {
        (row["legacy_fact_type"], row["legacy_predicate"]) for row in covered
    }
    publication = next(
        row for row in result["rows"] if row["predicate"] == "publication_state"
    )
    assert publication["mapping_kind"] == "approximate"


def test_legacy_fact_mapping_exact_and_character_rules():
    assert audit.legacy_fact_mapping("world_clock", "time")[0:2] == (
        "temporal_state", "time",
    )
    assert audit.legacy_fact_mapping("bakery:wild-bread:storefront", "operation_state")[0:2] == (
        "presence_state", "operation_state",
    )
    assert audit.legacy_fact_mapping("character:lin-wan", "location")[0:2] == (
        "location_state", "location",
    )
    assert audit.legacy_fact_mapping("article:lin-wan", "publication_state")[0:2] == (
        "continuity_state", "publication_state",
    )
    assert audit.legacy_fact_mapping("world_clock", "epoch") is None


def test_gold_audit_is_consistent_and_reports_frame_exclusion_gap():
    result = audit.audit_gold()
    assert result["audit"]["consistent"] is True
    assert result["audit"]["ledger_entries"] == 3
    assert result["audit"]["state_frame_assertions"] == 0
    assert result["audit"]["state_frame_excluded_assertions"] == 3
    assert any(
        observation.startswith("state_frame_all_assertions_excluded")
        for observation in result["audit"]["observations"]
    )
    assert result["idempotent_replay"]["skipped_as_duplicate"] is True
    assert result["idempotent_replay"]["after_hash_matches"] is True
    assert result["legacy_coverage"]["covered_count"] == 16
    assert result["legacy_coverage"]["no_legacy_equivalent_count"] == 0
    assert result["legacy_coverage"]["exact_mapping_count"] == 8
    assert result["legacy_coverage"]["approximate_mapping_count"] == 8
    assert result["legacy_state_frame"]["facts_count"] == 16
    assert result["legacy_state_frame"]["source_manifest_count"] == 16
    assert result["legacy_state_frame"]["frame_status"] == "complete"
    assert len(result["legacy_state_frame"]["frame_hash"]) == 64
    assert result["handover_projection"]["note"]["new_facts"]
    assert (
        result["handover_projection"]["field_coverage"]["foreshadowing"]["status"]
        == "legacy_only_not_projected"
    )
    assert result["character_projection"]["coverage"]["character_count"] == 3
    assert (
        result["character_projection"]["coverage"]["relations_status"]
        == "legacy_only_not_projected"
    )
    assert result["rag_metadata"]["metadata"]["characters"]
    assert result["world_state_facts"]["count"] == 16
    assert result["reviewer_context"]["coverage"]["handover_chain_status"] == "projected_from_wr"
    assert (
        result["reviewer_context"]["coverage"]["relation_context_status"]
        == "legacy_only_not_projected"
    )
    assert result["checkpoint_shadow"]["verified"] is True
    assert result["checkpoint_shadow"]["issues"] == []


def test_canary_audit_reads_committed_artifacts():
    if not audit.CANARY_RUNTIME.exists():
        pytest.skip("c21r4 runtime absent")
    result = audit.audit_canary()
    assert result["all_consistent"] is True
    assert [item["before_revision"] for item in result["subsections"]] == [7, 8, 9]
    assert [item["after_revision"] for item in result["subsections"]] == [8, 9, 10]
    assert all(
        item["state_frame_excluded_assertions"] == item["ledger_entries"]
        for item in result["subsections"]
    )
    assert all(
        item["legacy_state_frame"]["facts_count"] == 16
        for item in result["subsections"]
    )
    assert all(
        len(item["legacy_state_frame"]["frame_hash"]) == 64
        for item in result["subsections"]
    )
    assert all(
        item["handover_projection"]["field_coverage"]["new_facts"]["status"]
        == "projected_from_wr"
        for item in result["subsections"]
    )
    assert all(
        item["character_projection"]["coverage"]["character_count"] >= 1
        for item in result["subsections"]
    )
    assert all(
        item["rag_metadata"]["metadata"]["characters"]
        for item in result["subsections"]
    )
    assert all(
        item["reviewer_context"]["world_review_summary"]["fact_count"] == 16
        for item in result["subsections"]
    )
    assert all(
        item["checkpoint_shadow"]["verified"] is True
        for item in result["subsections"]
    )


def test_legacy_state_frame_is_deterministic():
    result = audit.audit_gold()
    second = audit.audit_gold()
    assert result["legacy_state_frame"]["frame_hash"] == second["legacy_state_frame"]["frame_hash"]
