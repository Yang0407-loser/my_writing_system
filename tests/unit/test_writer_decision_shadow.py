from __future__ import annotations

import inspect
import json
from copy import deepcopy
from pathlib import Path

import pytest

from experiments.writer_decision_shadow.aggregate import aggregate_reviews
from experiments.writer_decision_shadow.corpus import build_shadow_corpus
from experiments.writer_decision_shadow.models import (
    SceneDecisionTicket,
    ShadowCorpus,
)
from experiments.writer_decision_shadow.review import (
    build_review_template,
    validate_reviews,
)
from experiments.writer_decision_shadow.runner import (
    aggregate_shadow,
    build_shadow_package,
)
from experiments.writer_decision_shadow.ticket import compile_ticket


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = (
    ROOT
    / "experiments"
    / "style_control"
    / "fixtures"
    / "style_contract_ablation_action_bridge_manifest.json"
)
PUBLIC = (
    ROOT
    / "outputs"
    / "style-contract-ablation-action-bridge-real"
    / "blind-review-public.json"
)


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _public() -> tuple[str, dict]:
    raw = PUBLIC.read_text(encoding="utf-8")
    return raw, json.loads(raw)


def _ticket_and_corpus() -> tuple[SceneDecisionTicket, ShadowCorpus]:
    ticket = compile_ticket(_manifest())
    raw, public = _public()
    corpus = build_shadow_corpus(public, source_public_raw=raw)
    return ticket, corpus


def _completed_review(
    ticket: SceneDecisionTicket,
    corpus: ShadowCorpus,
    reviewer_id: str,
) -> dict:
    payload = build_review_template(ticket, corpus, reviewer_id=reviewer_id)
    for sample in payload["samples"]:
        paragraph_id = corpus.samples[0].paragraphs[0].paragraph_id
        modes = {
            item.decision_id: item.verification_mode
            for item in ticket.hard_obligations
        }
        for row in sample["hard_obligations"]:
            if modes[row["decision_id"]] in {"presence", "state_match"}:
                row["status"] = "present"
                row["evidence_paragraphs"] = [paragraph_id]
            else:
                row["status"] = "respected"
            row["confidence"] = 4
        for row in sample["soft_topology"]:
            row["status"] = "pass"
            row["evidence_paragraphs"] = [paragraph_id]
            row["confidence"] = 4
        for row in [
            *sample["unauthorized_content"],
            *sample["process_log_checks"],
        ]:
            row["detected"] = False
    return payload


def test_ticket_is_deterministic_traceable_and_has_no_content_authority():
    first = compile_ticket(_manifest())
    second = compile_ticket(_manifest())
    assert first == second
    assert first.ticket_hash == second.ticket_hash
    assert first.relationship_delta == "none"
    assert first.new_content_facts == []
    assert first.content_authority_owner == "upstream_scene_contract"
    assert first.deterministic is True
    assert first.ticket_token_estimate <= 250
    known = {item.ref_id for item in first.source_refs}
    obligations = [
        *first.hard_obligations,
        *first.soft_topology_obligations,
    ]
    assert obligations
    assert all(set(item.source_refs) <= known for item in obligations)
    assert {item.decision_id for item in first.hard_obligations} == {
        "M1", "M2", "M3", "M4", "H5", "H6", "H7", "H8", "H9", "H10", "H11"
    }
    assert {item.decision_id for item in first.soft_topology_obligations} == {
        "S1", "S2", "S3", "S4", "S5", "S6"
    }


def test_ticket_compiler_has_no_prose_or_path_input_and_compact_is_not_a_template():
    parameters = set(inspect.signature(compile_ticket).parameters)
    assert parameters == {"manifest"}
    compact = compile_ticket(_manifest()).compact_rendering
    for forbidden in (
        "许栀把钥匙",
        "像走进一个正在发酵的胃",
        "这不是客气，是划边界",
        "具体对白原句",
        "固定段落",
    ):
        assert forbidden not in compact


def test_frozen_corpus_order_hash_and_paragraph_roundtrip():
    raw, public = _public()
    corpus = build_shadow_corpus(public, source_public_raw=raw)
    assert corpus.sample_count == 6
    assert [item.blind_id for item in corpus.samples] == [
        item["blind_id"] for item in public["samples"]
    ]
    for source, frozen in zip(public["samples"], corpus.samples):
        restored = frozen.paragraph_separator.join(
            paragraph.text for paragraph in frozen.paragraphs
        )
        assert restored == source["text"]
        assert all(
            paragraph.paragraph_id == f"P{index:03d}"
            for index, paragraph in enumerate(frozen.paragraphs, 1)
        )


def test_build_package_has_no_arm_leak_and_creates_blank_templates(tmp_path: Path):
    result = build_shadow_package(MANIFEST, PUBLIC, tmp_path)
    assert result["sample_count"] == 6
    assert result["reviews_present"] == 0
    assert result["aggregate_ready"] is False
    public_files = [
        tmp_path / "decision-ticket-public.json",
        tmp_path / "shadow-corpus-public.json",
        tmp_path / "decision-witness-review-template.json",
        tmp_path / "decision-witness-review-instructions.md",
    ]
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in public_files)
    for secret in ('"arm"', '"sample_id"', "D2A", "D2"):
        assert secret not in public_text
    assert len(list((tmp_path / "review-templates").glob("validator-*.template.json"))) == 3
    assert not list((tmp_path / "reviews").glob("validator-*.json"))
    provenance = json.loads(
        (tmp_path / "decision-ticket-provenance.private.json").read_text(encoding="utf-8")
    )
    assert provenance["ticket_compiled_before_corpus_read"] is True
    assert provenance["llm_calls_made"] == 0
    assert provenance["new_prose_generated"] is False
    assert provenance["production_code_changed"] is False


def test_review_validation_accepts_three_complete_independent_reviews():
    ticket, corpus = _ticket_and_corpus()
    raw_reviews = [
        _completed_review(ticket, corpus, f"validator-{index:02d}")
        for index in range(1, 4)
    ]
    reviews, validation = validate_reviews(ticket, corpus, raw_reviews)
    assert len(reviews) == 3
    assert validation["valid"] is True
    aggregate = aggregate_reviews(ticket, corpus, raw_reviews)
    assert aggregate["ticket_source_coverage"]["coverage"] == 1.0
    assert aggregate["hard_decision_agreement"]["pairwise_agreement"] == 1.0
    assert aggregate["soft_topology_agreement"]["pairwise_agreement"] == 1.0
    assert aggregate["single_total_score_prohibited"] is True
    assert aggregate["route_effect_conclusion_allowed"] is False


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("bad_paragraph", "unknown paragraph"),
        ("missing_evidence", "requires evidence"),
        ("string_confidence", "schema validation"),
        ("out_of_range_confidence", "schema validation"),
        ("sample_order", "sample IDs/order"),
        ("obligation_order", "hard obligation IDs/order"),
    ],
)
def test_review_validation_rejects_invalid_or_incomplete_reviews(mutation: str, match: str):
    ticket, corpus = _ticket_and_corpus()
    raw_reviews = [
        _completed_review(ticket, corpus, f"validator-{index:02d}")
        for index in range(1, 4)
    ]
    target = raw_reviews[0]["samples"][0]["hard_obligations"][0]
    if mutation == "bad_paragraph":
        target["evidence_paragraphs"] = ["P999"]
    elif mutation == "missing_evidence":
        target["evidence_paragraphs"] = []
    elif mutation == "string_confidence":
        target["confidence"] = "4"
    elif mutation == "out_of_range_confidence":
        target["confidence"] = 6
    elif mutation == "sample_order":
        raw_reviews[0]["samples"][0], raw_reviews[0]["samples"][1] = (
            raw_reviews[0]["samples"][1],
            raw_reviews[0]["samples"][0],
        )
    else:
        rows = raw_reviews[0]["samples"][0]["hard_obligations"]
        rows[0], rows[1] = rows[1], rows[0]
    with pytest.raises(ValueError, match=match):
        validate_reviews(ticket, corpus, raw_reviews)


def test_review_validation_rejects_duplicate_reviewer_and_does_not_autofill():
    ticket, corpus = _ticket_and_corpus()
    raw_reviews = [
        _completed_review(ticket, corpus, "validator-01")
        for _ in range(3)
    ]
    snapshot = deepcopy(raw_reviews)
    with pytest.raises(ValueError, match="unique"):
        validate_reviews(ticket, corpus, raw_reviews)
    assert raw_reviews == snapshot


def test_aggregate_command_refuses_incomplete_reviews(tmp_path: Path):
    build_shadow_package(MANIFEST, PUBLIC, tmp_path)
    with pytest.raises(ValueError, match="expected 3"):
        aggregate_shadow(tmp_path)
    assert not (tmp_path / "decision-witness-aggregate.json").exists()


def test_experiment_modules_are_outside_production_writer():
    module_path = Path(inspect.getfile(compile_ticket)).as_posix()
    assert "/experiments/writer_decision_shadow/" in module_path
    assert "/app/" not in module_path
