import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "real-canary-constraint-detection-attribution.json"

ALLOWED_ATTRIBUTIONS = {
    "true_missing_event",
    "partial_event_completion",
    "semantic_paraphrase_false_negative",
    "pronoun_or_ellipsis_false_negative",
    "cross_sentence_false_negative",
    "state_or_tense_misclassification",
    "unstable_keyword_selection",
    "overly_strict_threshold",
    "overplanned_arc_milestone",
    "insufficient_evidence",
    "unavailable_generation_attempt",
}


def _load():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_two_detector_chains_are_counted_separately():
    data = _load()
    mandatory = data["mandatory_event_detection"]["records"]
    arcs = data["arc_post_check"]["records"]
    assert len(mandatory) == 8
    assert len(arcs) == 15
    assert sum(item["triggered_extra_writer_call"] for item in mandatory) == 8
    assert data["arc_post_check"]["metrics"]["extra_writer_calls"] == 0


def test_every_result_has_an_allowed_attribution():
    data = _load()
    records = (
        data["mandatory_event_detection"]["records"]
        + data["arc_post_check"]["records"]
    )
    assert all(item["attribution"] in ALLOWED_ATTRIBUTIONS for item in records)


def test_unavailable_attempts_are_not_counted_as_true_or_false_positives():
    metrics = _load()["mandatory_event_detection"]["metrics"]
    assert metrics["confirmed_precision"] is None
    assert metrics["confirmed_precision_denominator"] == 0
    assert metrics["false_positive_rate"] is None
    assert metrics["unavailable_checks"] == 8


def test_arc_counts_and_precision_formulas_are_consistent():
    data = _load()
    records = data["arc_post_check"]["records"]
    counts = Counter(item["actually_completed"] for item in records)
    metrics = data["arc_post_check"]["metrics"]
    assert counts[False] == metrics["true_missing"] == 1
    assert counts["partial"] == metrics["partial_completion"] == 6
    assert counts[True] == metrics["complete_semantic_false_negatives"] == 8
    assert metrics["strict_missing_precision"] == round(1 / 15, 4)
    assert metrics["actionable_missing_or_partial_precision"] == round(7 / 15, 4)
    assert metrics["complete_false_positive_rate"] == round(8 / 15, 4)


def test_evidence_excerpts_are_short_hashed_and_traceable_when_source_exists():
    data = _load()
    output = data["sources"]["final_output"]
    source = ROOT / output["path"]
    assert output["committed"] is False
    if not source.exists():
        return

    raw = source.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == output["sha256"]
    text = raw.decode("utf-8")
    for item in data["arc_post_check"]["records"]:
        assert item["source_id"].startswith("event_graph:")
        assert hashlib.sha256(item["required_event"].encode("utf-8")).hexdigest() == item["text_hash"]
        for span in item["evidence_spans"]:
            assert 0 <= span["start"] < span["end"] <= len(text)
            assert len(span["excerpt"]) <= 140
            assert text[span["start"]:span["end"]] == span["excerpt"]


def test_runtime_cost_counts_only_subsection_draft_retries():
    cost = _load()["runtime_cost"]
    assert cost["planned_subsection_draft_calls"] == 4
    assert cost["actual_subsection_draft_calls"] == 12
    assert cost["mandatory_retry_calls"] == 8
    assert sum(cost["retry_calls_by_subsection"].values()) == 8
    assert round(sum(cost["retry_stream_latency_seconds_by_subsection"].values()), 1) == 242.2
    assert cost["incremental_retry_tokens"] is None
    assert cost["incremental_retry_cost_usd"] is None


def test_arc_edge_accounting_and_single_decision_are_consistent():
    data = _load()
    planning = data["arc_planning"]
    assert planning["milestones"] == 15
    assert sum(planning["milestones_by_character"].values()) == 15
    assert sum(planning["milestones_by_subsection"].values()) == 15
    assert sum(planning["requiredness_counts"].values()) == 15
    assert planning["same_character_consecutive_links"] == 10
    assert planning["same_section_pairwise_links"] == 105
    assert planning["link_operations"] == 115
    assert planning["proven_causal_links"] == 0
    assert data["decision"]["recommended_next_step"] == "A"
    assert data["decision"]["not_executed"] is True


def test_audit_did_not_call_writer_or_change_production():
    scope = _load()["scope"]
    assert scope["writer_llm_calls"] == 0
    assert scope["new_generation_runs"] == 0
    assert scope["production_changes"] == 0
