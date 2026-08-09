from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.writer_boundary_canary.aggregate import aggregate_reviews
from experiments.writer_boundary_canary.anonymise import anonymise
from experiments.writer_boundary_canary.boundary_ticket import mock_ticket, validate_ticket
from experiments.writer_boundary_canary.models import BoundaryReview, BoundaryTicket, CompiledSummary
from experiments.writer_boundary_canary.prompts import boundary_prompt, shared_contract, w0_prompt, w2_prompt
from experiments.writer_boundary_canary.runner import DEFAULT_FIXTURE, build_plan, estimate_budget, run
from experiments.writer_boundary_canary.summary_compiler import compile_summary, validate_summary


def fx():
    return json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))


def rows():
    return [{"arm": arm, "repeat": r, "text": f"占位 {r}-{i}。\n\n第二段。", "consumed_ticket_hash": "t" if arm == "W2" else None, "consumed_summary_hash": "s" if arm == "W2" else None} for r in (1, 2) for i, arm in enumerate(("W0", "W2"), 1)]


def filled(template, rid, choice="text_1"):
    value = copy.deepcopy(template); value["reviewer_id"] = rid
    for sample in value["samples"]:
        sample["hard_checks"] = {"mandatory_events_complete": True, "new_character": False, "new_solution": False, "relationship_change": False, "temporary_ending": True, "boundary_fidelity": True}
        for group in ("original_witnesses", "structural_diagnostics"):
            for witness in sample[group]: witness["detected"] = False
    for pair in value["pairs"]:
        for field in ("naturalness", "less_template", "character_credibility", "emotional_residue", "overall_quality", "more_mechanical"): pair[field] = choice
        pair["confidence"] = 3
    return value


def test_sc4_is_new_non_power_workshop_scene():
    data = fx()
    assert data["scene_id"] == "SC4"
    assert "暴雨" in data["scene"] and "阅览室" in data["scene"]
    assert "停电" not in data["scene"] and "工坊" not in data["scene"]


def test_ticket_whitelist_hash_frozen_and_no_organization_fields():
    data = fx(); ticket = validate_ticket(mock_ticket(data, 1), data)
    assert ticket.ticket_hash == mock_ticket(data, 1).ticket_hash
    rendered = json.dumps(ticket.model_dump())
    for term in ("initial_risk_check", "expand_focus", "dialogue_jobs", "emotion_channels", "writer_freedoms", "chain_of_thought"):
        assert term not in rendered
    with pytest.raises(ValidationError): ticket.repeat = 2


def test_summary_is_deterministic_non_model_and_validated():
    data = fx(); ticket = mock_ticket(data, 1)
    a = compile_summary(ticket); b = compile_summary(ticket)
    assert a == b and a.model_calls == 0
    assert validate_summary(a, ticket) == a
    assert a.summary_hash == b.summary_hash


def test_summary_contains_no_schema_or_list_language():
    summary = compile_summary(mock_ticket(fx(), 1)).compiled_summary
    for term in ("priority_object", "store_item_temporary_handling", "dialogue_jobs", "emotion_channels", "writer_freedoms", "{", "•"):
        assert term not in summary
    assert "\n" not in summary


def test_boundary_prompt_declares_exact_output_key_and_scalar_choice():
    prompt = boundary_prompt(fx(), 1)
    assert '"store_item_temporary_handling"' in prompt
    assert '"required_output_key": "store_item_temporary_handling"' in prompt
    assert '"choose_exactly": 1' in prompt
    assert "该键的值必须是一个字符串，不能是数组" in prompt
    assert "禁止输出 allowed_values、allowed_handling 或任何其他键" in prompt
    assert '"allowed_handling"' not in prompt


def test_w0_w2_share_inputs_but_only_w2_has_natural_boundary():
    data = fx(); summary = compile_summary(mock_ticket(data, 1))
    w0, w2 = w0_prompt(data), w2_prompt(data, summary)
    for key, value in shared_contract(data).items():
        assert json.loads(w0.split("\n", 1)[1])[key] == value
        assert json.loads(w2.split("\n", 1)[1])[key] == value
    assert summary.compiled_summary in w2 and summary.compiled_summary not in w0
    for term in ("locked_boundaries", "ticket_hash", "dialogue_jobs", "emotion_channels", "writer_freedoms"):
        assert term not in w2


def test_plan_exact_six_calls_and_private_freedoms(tmp_path: Path):
    plan = build_plan(DEFAULT_FIXTURE, tmp_path)
    assert plan["planned_call_count"] == 6 and plan["planned_text_count"] == 4
    assert plan["planned_ticket_count"] == 2 and plan["planned_summary_count"] == 2
    assert sum(x["arm"] == "W0" and x["stage"] == "prose" for x in plan["calls"]) == 2
    assert sum(x["arm"] == "W2" and x["stage"] == "ticket" for x in plan["calls"]) == 2
    assert "writer_freedoms_private_metadata_only" in plan


def test_real_disabled_mock_resume_and_hash_consumption(tmp_path: Path):
    with pytest.raises(PermissionError): run(DEFAULT_FIXTURE, tmp_path, backend="llm")
    build_plan(DEFAULT_FIXTURE, tmp_path)
    assert run(DEFAULT_FIXTURE, tmp_path, backend="mock")["route_evidence"] is False
    assert run(DEFAULT_FIXTURE, tmp_path, backend="mock")["completed_calls"] == 6
    result = json.loads((tmp_path / "results/SC4__W2__r1.json").read_text(encoding="utf-8"))
    assert result["consumed_ticket_hash"] and result["consumed_summary_hash"]
    assert result["placeholder_not_fiction"] is True


def test_tampered_summary_blocks_resume(tmp_path: Path):
    build_plan(DEFAULT_FIXTURE, tmp_path); run(DEFAULT_FIXTURE, tmp_path, backend="mock")
    path = tmp_path / "summaries/SC4__W2__r1.json"; raw = json.loads(path.read_text(encoding="utf-8"))
    raw["compiled_summary"] += "篡改"; path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError): run(DEFAULT_FIXTURE, tmp_path, backend="mock")


def test_anonymise_four_texts_two_pairs_without_route_leak():
    public, private, template = anonymise(rows(), 7)
    assert len(public["texts"]) == 4 and len(public["pairs"]) == 2
    assert len(private["mapping"]) == 4
    rendered = json.dumps(public, ensure_ascii=False)
    for term in ("W0", "W2", "Boundary Ticket", "Boundary Maker", "single-pass", "Realizer", "ticket_hash", "summary_hash", "backend"):
        assert term not in rendered
    assert len(template["samples"][0]["original_witnesses"]) == 5
    assert len(template["samples"][0]["structural_diagnostics"]) == 5


def test_review_strict_confidence_and_witness_evidence():
    _, _, template = anonymise(rows(), 7); review = filled(template, "r1")
    review["pairs"][0]["confidence"] = "high"
    with pytest.raises(ValidationError): BoundaryReview.model_validate(review)
    review = filled(template, "r1")
    review["samples"][0]["structural_diagnostics"][0]["detected"] = True
    with pytest.raises(ValidationError): BoundaryReview.model_validate(review)


def test_aggregate_three_review_gate_no_total_score_and_negative_metric():
    public, private, template = anonymise(rows(), 7)
    with pytest.raises(ValueError): aggregate_reviews(public, private, [filled(template, "r1")])
    result = aggregate_reviews(public, private, [filled(template, f"r{i}", "tie") for i in range(1, 4)])
    assert result["single_total_score"] is None
    assert result["w2_preference_shares_excluding_ties"]["more_mechanical"] == 0


def test_budget_six_calls_and_zero_compiler_tokens():
    budget = estimate_budget()
    assert budget["real_call_count"] == 6
    assert budget["boundary_maker_calls"] == 2 and budget["prose_calls"] == 4
    assert budget["summary_compiler_model_tokens"] == 0
