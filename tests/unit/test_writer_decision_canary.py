from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.writer_decision_canary.aggregate import aggregate_reviews
from experiments.writer_decision_canary.anonymise import anonymise
from experiments.writer_decision_canary.models import CanaryReview, SelectedDecisions
from experiments.writer_decision_canary.prompts import (
    shared_prose_contract,
    w0_prompt,
    w1_prompt,
)
from experiments.writer_decision_canary.runner import (
    DEFAULT_FIXTURE,
    build_plan,
    estimate_budget,
    run,
)
from experiments.writer_decision_canary.ticket import (
    frozen_snapshot,
    mock_ticket,
    validate_ticket,
)


def fixture() -> dict:
    return json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))


def mock_results() -> list[dict]:
    return [
        {
            "arm": arm,
            "repeat": repeat,
            "text": f"占位文本 {repeat}-{index}。\n\n第二段。",
            "consumed_ticket_hash": "abc" if arm == "W1" else None,
        }
        for repeat in (1, 2)
        for index, arm in enumerate(("W0", "W1"), 1)
    ]


def completed_review(template: dict, reviewer_id: str, preference: str = "text_1") -> dict:
    row = copy.deepcopy(template)
    row["reviewer_id"] = reviewer_id
    for sample in row["samples"]:
        sample["hard_checks"] = {
            "mandatory_events_complete": True,
            "new_character": False,
            "new_solution": False,
            "relationship_change": False,
            "temporary_ending": True,
            "decision_fidelity": True,
        }
        for witness in sample["witnesses"]:
            witness["detected"] = False
    for pair in row["pairs"]:
        for field in (
            "naturalness", "less_template", "character_credibility",
            "emotional_residue", "overall_quality", "more_mechanical",
        ):
            pair[field] = preference
        pair["confidence"] = 3
    return row


def test_sc3_is_new_independent_workshop_scene():
    data = fixture()
    assert data["scene_id"] == "SC3"
    assert "修复工坊" in data["scene"]
    assert "仓库" not in data["scene"]


def test_w0_w1_share_content_style_and_prose_parameters():
    data = fixture()
    ticket = mock_ticket(data, 1)
    shared = shared_prose_contract(data)
    w0_payload = json.loads(w0_prompt(data).split("\n", 1)[1])
    w1_payload = json.loads(w1_prompt(data, ticket).split("\n", 1)[1])
    assert all(w0_payload[key] == value for key, value in shared.items())
    assert all(w1_payload[key] == value for key, value in shared.items())
    assert data["prose"] == data["prose"]


def test_w0_has_no_ticket_and_w1_consumes_valid_ticket():
    data = fixture()
    ticket = validate_ticket(mock_ticket(data, 1), data)
    assert "locked_decisions" not in w0_prompt(data)
    prompt = w1_prompt(data, ticket)
    assert ticket.ticket_hash in prompt


def test_ticket_whitelist_and_no_reasoning_fields():
    data = fixture()
    ticket = mock_ticket(data, 1)
    keys = set(ticket.selected_decisions.model_dump())
    assert not {"reason", "rationale", "analysis", "chain_of_thought"} & keys
    bad = ticket.selected_decisions.model_dump()
    bad["initial_risk_check"] = "invent_a_plot"
    with pytest.raises(ValidationError):
        SelectedDecisions.model_validate(bad)


def test_ticket_hash_stable_and_ticket_frozen():
    data = fixture()
    left = mock_ticket(data, 1)
    right = mock_ticket(data, 1)
    assert left.ticket_hash == right.ticket_hash
    assert frozen_snapshot(left) == frozen_snapshot(right)
    with pytest.raises(ValidationError):
        left.repeat = 2


def test_w1_prompt_excludes_bridge_demos_evidence_and_long_lists():
    data = fixture()
    prompt = w1_prompt(data, mock_ticket(data, 1))
    for prohibited in ("ActionStyleBridge", "demonstrations", "evidence", "negative_reasons"):
        assert prohibited not in prompt
    assert "M1 " not in prompt
    assert "F1 " not in prompt


def test_plan_has_six_calls_and_exact_matrix(tmp_path: Path):
    plan = build_plan(DEFAULT_FIXTURE, tmp_path)
    assert plan["planned_call_count"] == 6
    assert plan["planned_text_count"] == 4
    counts = {(arm, stage): 0 for arm in ("W0", "W1") for stage in ("ticket", "prose")}
    for call in plan["calls"]:
        counts[(call["arm"], call["stage"])] += 1
    assert counts[("W0", "ticket")] == 0
    assert counts[("W0", "prose")] == 2
    assert counts[("W1", "ticket")] == 2
    assert counts[("W1", "prose")] == 2


def test_real_calls_disabled_by_default(tmp_path: Path):
    with pytest.raises(PermissionError):
        run(DEFAULT_FIXTURE, tmp_path, backend="llm")


def test_mock_resume_and_targeted_failed_retry(tmp_path: Path):
    build_plan(DEFAULT_FIXTURE, tmp_path)
    first = run(DEFAULT_FIXTURE, tmp_path, backend="mock")
    assert first["route_evidence"] is False
    second = run(DEFAULT_FIXTURE, tmp_path, backend="mock")
    assert second["completed_calls"] == 6
    failed_path = tmp_path / "tickets" / "SC3__W1__r1.json"
    failed_path.write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    retried = run(
        DEFAULT_FIXTURE, tmp_path, backend="mock",
        rerun_id="SC3__W1__r1:ticket",
    )
    assert retried["completed_calls"] == 1
    assert json.loads(failed_path.read_text(encoding="utf-8"))["locked"] is True


def test_mock_outputs_four_texts_two_pairs_and_no_identity_leak():
    public, private, _ = anonymise(mock_results(), 42)
    assert len(public["texts"]) == 4
    assert len(public["pairs"]) == 2
    assert len(private["mapping"]) == 4
    rendered = json.dumps(public, ensure_ascii=False)
    for prohibited in ("W0", "W1", "Decision Ticket", "single-pass", "sample_id", "ticket_hash"):
        assert prohibited not in rendered


def test_review_confidence_is_strict_integer_and_all_scope_fields_required():
    public, _, template = anonymise(mock_results(), 42)
    review = completed_review(template, "reviewer-1")
    review["pairs"][0]["confidence"] = "high"
    with pytest.raises(ValidationError):
        CanaryReview.model_validate(review)
    assert public["pairs"]


def test_detected_witness_requires_paragraph_and_description():
    _, _, template = anonymise(mock_results(), 42)
    review = completed_review(template, "reviewer-1")
    review["samples"][0]["witnesses"][0]["detected"] = True
    with pytest.raises(ValidationError):
        CanaryReview.model_validate(review)


def test_aggregate_requires_exactly_three_independent_reviews():
    public, private, template = anonymise(mock_results(), 42)
    reviews = [completed_review(template, f"r{i}") for i in range(2)]
    with pytest.raises(ValueError, match="exactly three"):
        aggregate_reviews(public, private, reviews)


def test_aggregate_has_no_single_total_score_and_split_is_uncertain():
    public, private, template = anonymise(mock_results(), 42)
    reviews = [
        completed_review(template, "r1", "text_1"),
        completed_review(template, "r2", "text_2"),
        completed_review(template, "r3", "tie"),
    ]
    result = aggregate_reviews(public, private, reviews)
    assert result["single_total_score"] is None
    assert result["conclusion"] == "uncertain"


def test_budget_counts_six_real_calls():
    budget = estimate_budget()
    assert budget["real_call_count"] == 6
    assert budget["decision_calls"] == 2
    assert budget["prose_calls"] == 4
