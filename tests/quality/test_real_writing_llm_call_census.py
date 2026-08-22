import json
from pathlib import Path

from app.config import settings


ROOT = Path(__file__).resolve().parents[2]
CENSUS_PATH = ROOT / "reports" / "real-writing-llm-call-census.json"
NORMALIZATION_PATH = ROOT / "reports" / "foreshadowing-resolve-chapter-normalization.json"

ALLOWED_PURPOSES = {
    "style_behavior",
    "handover_brief",
    "subsection_draft",
    "handover_extraction",
    "subsection_expansion",
    "character_state_update",
    "consistency_check",
    "continuity_check",
    "review",
    "foreshadowing_check",
    "other_identified",
    "unknown",
}


def _census():
    return json.loads(CENSUS_PATH.read_text(encoding="utf-8"))


def test_call_ledger_has_all_25_http_calls_and_four_drafts():
    report = _census()
    calls = report["calls"]
    assert report["reconciliation"]["http_post_count"] == 25
    assert len(calls) == 25
    assert [call["call_index"] for call in calls] == list(range(1, 26))
    assert sum(call["main_draft_call"] for call in calls) == 4
    assert report["invariants"]["one_draft_call_per_subsection"] is True
    assert report["invariants"]["mandatory_event_retry_calls"] == 0
    assert all(call["purpose"] in ALLOWED_PURPOSES for call in calls)


def test_purpose_groups_sum_to_call_total():
    report = _census()
    grouped_count = sum(item["calls"] for item in report["by_purpose"].values())
    assert grouped_count == len(report["calls"]) == 25
    assert sum(item["calls"] for item in report["lifecycle"].values()) == 25


def test_unavailable_stream_tokens_are_not_fabricated_or_summed():
    report = _census()
    draft_calls = [call for call in report["calls"] if call["purpose"] == "subsection_draft"]
    assert len(draft_calls) == 4
    assert all(call["actual_input_tokens"] is None for call in draft_calls)
    assert all(call["output_tokens"] is None for call in draft_calls)
    assert report["reconciliation"]["stream_draft_token_samples_unavailable"] == 4

    known_total = sum(
        call["actual_input_tokens"] + call["output_tokens"]
        for call in report["calls"]
        if call["actual_input_tokens"] is not None and call["output_tokens"] is not None
    )
    assert known_total == report["reconciliation"]["known_per_call_token_lower_bound"] == 50022


def test_logged_total_and_background_gap_are_explicit():
    report = _census()["reconciliation"]
    assert sum(report["logged_agent_breakdown"].values()) == report["logged_total_tokens"] == 39010
    assert report["writer_recovered_non_stream_tokens"] == 32442
    assert report["background_thread_tokens_excluded_from_logged_total"] == 11012


def test_character_arc_v2_is_closed_without_changing_default():
    closeout = _census()["character_arc_contract_v2"]
    assert closeout["status"] == "experimental_not_promoted"
    assert closeout["new_v2_planner_ran"] is False
    assert closeout["compatibility_classification"] == {
        "soft_arc_progress": 12,
        "hard_arc_transition": 0,
    }
    assert closeout["source_id_coverage"] == "0/12"
    assert closeout["source_hash_coverage"] == "0/12"
    assert closeout["v1_theoretical_link_operations"] == 73
    assert closeout["v2_actual_edges"] == 0
    assert closeout["production_default"] == "v1"
    assert settings.CHARACTER_ARC_CONTRACT_VERSION == "v1"


def test_reports_record_zero_new_llm_calls_and_no_private_payload_fields():
    census = _census()
    normalization = json.loads(NORMALIZATION_PATH.read_text(encoding="utf-8"))
    assert census["scope"]["writer_llm_calls"] == 0
    assert census["scope"]["new_generation_runs"] == 0
    assert normalization["forbidden_actions"]["llm_calls"] == 0

    serialized = CENSUS_PATH.read_text(encoding="utf-8")
    forbidden_keys = ('"prompt"', '"messages"', '"full_text"', '"api_key"')
    assert all(key not in serialized.lower() for key in forbidden_keys)
    assert all(call["source_line"] > 0 for call in census["calls"])
