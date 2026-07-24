import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = (
    ROOT
    / "reports"
    / "state-frame-real-four-subsection-acceptance-2026-07-25.json"
)


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_real_four_subsection_acceptance_counts_and_scope():
    payload = _report()
    assert payload["status"] == "accepted"
    assert payload["task"]["status"] == "completed"
    assert payload["task"]["completed_subsections"] == 4
    assert payload["history"]["before_frames"] == 4
    assert payload["history"]["after_frames"] == 4
    assert payload["history"]["deltas"] == 4
    assert payload["history"]["pending_before"] == 0
    assert payload["history"]["errors"] == 0
    assert payload["history"]["duplicate_record_ids"] == 0
    assert payload["scope"]["writer_llm_calls_added_by_acceptance"] == 0
    assert payload["scope"]["state_frame_injected_into_writer"] is False


def test_real_records_are_unique_traceable_and_non_production():
    payload = _report()
    records = payload["history"]["records"]
    assert len(records) == 4
    assert len({record["record_id"] for record in records}) == 4
    assert [record["subsection"] for record in records] == [1, 2, 3, 4]
    for record in records:
        assert record["before_frame_hash"]
        assert record["after_frame_hash"]
        assert record["delta_id"].startswith("statedelta:")
        assert record["output_sha256"]
        assert record["prompt_messages_hash"]
        assert record["commit_idempotency_key"].endswith(
            f":1:{record['subsection']}"
        )
        assert record["pending_source_types"] == []
        assert record["production_effect"] is False


def test_three_layers_restart_and_task_history_fallback_are_consistent():
    payload = _report()
    consistency = payload["storage_consistency"]
    assert consistency["blackboard"]["records"] == 4
    assert consistency["checkpoint"]["records"] == 4
    assert consistency["task_store"]["records"] == 4
    assert consistency["record_ids_equal"] is True
    assert consistency["before_hashes_equal"] is True
    assert consistency["after_hashes_equal"] is True
    assert consistency["delta_ids_equal"] is True

    restart = payload["worker_restart_recovery"]
    assert restart["worker_restarted"] is True
    assert restart["worker_ready"] is True
    assert restart["task_rerun"] is False
    assert restart["new_model_calls"] == 0
    assert restart["duplicate_records_created"] == 0
    assert all(
        restart[key] is True
        for key in (
            "record_ids_stable",
            "before_hashes_stable",
            "after_hashes_stable",
            "delta_ids_stable",
        )
    )

    fallback = payload["redis_unavailable_fallback"]
    assert fallback["redis_data_deleted"] is False
    assert fallback["database_modified"] is False
    assert fallback["source"] == "task_history"
    assert fallback["reconstructed"] is False
    assert fallback["recovered_subsections"] == 4
    assert fallback["recovery_rate"] == 1.0
    assert fallback["writer_or_llm_calls"] == 0


def test_acceptance_preserves_quality_and_privacy_boundaries():
    payload = _report()
    privacy = payload["traceability_and_privacy"]
    assert privacy["persisted_facts"] == 96
    assert privacy["facts_with_source_id_and_hash"] == 96
    assert privacy["source_hash_traceability_rate"] == 1.0
    assert privacy["maximum_evidence_excerpt_characters"] <= 140
    assert all(
        privacy[key] is False
        for key in (
            "full_story_text_present",
            "full_prompt_present",
            "messages_present",
            "api_key_present",
            "environment_contents_present",
            "database_or_chroma_contents_present",
        )
    )
    quality = payload["quality_assessability"]
    assert quality["handover_continuity"]["status"] == "unassessable"
    assert quality["character_state_transition"]["status"] == "partial"
    assert quality["foreshadow_health"]["status"] == "unassessable"
    assert quality["unavailable_is_quality_failure"] is False
    assert quality["final_state_backfilled_to_earlier_subsections"] is False
    assert quality["quality_truth_claimed"] is False
    assert payload["acceptance"]["all_mechanical_gates_passed"] is True
    assert payload["acceptance"]["state_frame_persistence_accepted"] is True
    assert payload["acceptance"]["writer_shadow_injection_authorized"] is False
