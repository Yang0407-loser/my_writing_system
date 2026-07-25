import hashlib
import json

from app.config import Settings
from app.writing.handover_contract_v2 import (
    HandoverClaim,
    HandoverContractValidatorV2,
    HandoverEvidence,
    HandoverOpenEvent,
    adapt_v2_to_legacy_handover_note,
    build_handover_sources,
    compile_next_boundary,
)


def _fixture():
    text = "林晚回到家。她把照片放进相册。她计划下周再去面包店。"
    current = {
        "subsection": 1,
        "title": "回家",
        "description": "林晚回家并整理照片。",
        "key_points": ["林晚整理照片"],
    }
    following = {
        "_section": 1,
        "subsection": 2,
        "title": "再访",
        "description": "下周林晚再次到店。",
        "key_points": ["林晚再次到店"],
    }
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=text,
        current_outline=current,
        next_outline=following,
    )
    generated = sources["generated-subsection:S1.1"]
    boundary = compile_next_boundary(
        section=1,
        subsection=1,
        current_outline=current,
        next_outline=following,
    )
    return text, generated, sources, boundary


def _evidence(source, excerpt):
    start = source.text.index(excerpt)
    return {
        "source_type": source.source_type,
        "source_id": source.source_id,
        "source_hash": source.source_hash,
        "start": start,
        "end": start + len(excerpt),
        "excerpt": excerpt,
    }


def _claim(source, *, claim_id="c1", predicate="回到", obj="家", **updates):
    value = {
        "claim_id": claim_id,
        "category": "location_state",
        "subject": "林晚",
        "predicate": predicate,
        "object": obj,
        "temporal_status": "current",
        "certainty": "confirmed",
        "evidence": [_evidence(source, "林晚回到家")],
        "claim_hash": "",
        "provenance": "handover_extractor_v2",
    }
    value.update(updates)
    return value


def test_config_defaults_to_v1_and_invalid_value_falls_back(monkeypatch):
    configured = Settings()
    assert configured.WRITER_HANDOVER_CONTRACT_VERSION == "v1"
    monkeypatch.setattr(
        configured, "WRITER_HANDOVER_CONTRACT_VERSION_RAW", "future"
    )
    monkeypatch.setattr(configured, "WRITER_HANDOVER_CONTRACT_VERSION", "v1")
    assert any(
        "WRITER_HANDOVER_CONTRACT_VERSION" in warning
        for warning in configured.validate()
    )


def test_evidence_span_and_hash_are_exact():
    _, source, sources, boundary = _fixture()
    result = HandoverContractValidatorV2().validate(
        {"claims": [_claim(source)]},
        sources=sources,
        next_boundary=boundary,
    )
    assert result.accepted_claim_count == 1
    assert result.rejected_claim_count == 0
    assert result.source_traceability_rate == 1.0

    bad_hash = _claim(source, claim_id="bad-hash")
    bad_hash["evidence"][0]["source_hash"] = "0" * 64
    mismatch = HandoverContractValidatorV2().validate(
        {"claims": [bad_hash]},
        sources=sources,
        next_boundary=boundary,
    )
    assert mismatch.rejection_counts == {"source_hash_mismatch": 1}

    bad_text = _claim(source, claim_id="bad-text")
    bad_text["evidence"][0]["excerpt"] = "林晚回到店"
    mismatch = HandoverContractValidatorV2().validate(
        {"claims": [bad_text]},
        sources=sources,
        next_boundary=boundary,
    )
    assert mismatch.rejection_counts == {"evidence_text_mismatch": 1}


def test_unsupported_psychology_is_rejected_but_observable_action_survives():
    text = "周野停顿了一下，把水递给林晚。"
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=text,
        current_outline={"subsection": 1, "title": "递水"},
        next_outline=None,
    )
    source = sources["generated-subsection:S1.1"]
    evidence = _evidence(source, text)
    payload = {
        "claims": [
            {
                "claim_id": "action",
                "category": "known_fact",
                "subject": "周野",
                "predicate": "递给",
                "object": "林晚",
                "temporal_status": "past",
                "certainty": "confirmed",
                "evidence": [evidence],
                "claim_hash": "",
                "provenance": "handover_extractor_v2",
            },
            {
                "claim_id": "mind",
                "category": "character_state",
                "subject": "周野",
                "predicate": "内心动摇",
                "object": "",
                "temporal_status": "current",
                "certainty": "confirmed",
                "evidence": [evidence],
                "claim_hash": "",
                "provenance": "handover_extractor_v2",
            },
        ]
    }
    result = HandoverContractValidatorV2().validate(
        payload,
        sources=sources,
        next_boundary=compile_next_boundary(
            section=1,
            subsection=1,
            current_outline={"subsection": 1, "title": "递水"},
            next_outline=None,
        ),
    )
    assert [item.claim_id for item in result.contract.end_state.claims] == [
        "action"
    ]
    assert result.rejection_counts == {"unsupported_psychology": 1}


def test_planned_conditional_and_explicit_unknown_do_not_become_facts():
    _, source, sources, boundary = _fixture()
    payload = {
        "claims": [
            _claim(
                source,
                claim_id="planned",
                predicate="计划",
                obj="下周再去面包店",
                category="known_fact",
                temporal_status="planned",
                evidence=[_evidence(source, "她计划下周再去面包店")],
                subject="她",
            ),
            _claim(
                source,
                claim_id="unknown",
                category="known_fact",
                temporal_status="unknown",
                certainty="explicit_unknown",
            ),
        ]
    }
    result = HandoverContractValidatorV2().validate(
        payload, sources=sources, next_boundary=boundary
    )
    note = adapt_v2_to_legacy_handover_note(result)
    assert note["new_facts"] == []


def test_stale_claim_and_completed_open_event_are_rejected():
    _, source, sources, boundary = _fixture()
    parsed = HandoverClaim.model_validate(_claim(source)).with_hash()
    payload = {
        "claims": [parsed.model_dump(mode="json")],
        "open_events": [
            {
                "event_id": "already-done",
                "actors": ["林晚"],
                "action": "整理",
                "object": "照片",
                "completion_status": "completed",
                "evidence": [_evidence(source, "她把照片放进相册")],
                "source_hash": source.source_hash,
            }
        ],
    }
    result = HandoverContractValidatorV2().validate(
        payload,
        sources=sources,
        next_boundary=boundary,
        stale_completed_claim_hashes=[parsed.claim_hash],
    )
    assert result.accepted_claim_count == 0
    assert result.rejection_counts == {"stale_completed_event": 2}
    assert adapt_v2_to_legacy_handover_note(result)["new_facts"] == []


def test_open_event_adapter_only_keeps_open_or_partial():
    _, source, sources, boundary = _fixture()
    payload = {
        "open_events": [
            {
                "event_id": "open",
                "actors": ["林晚"],
                "action": "继续整理",
                "object": "相册",
                "completion_status": "open",
                "evidence": [_evidence(source, "她把照片放进相册")],
                "source_hash": source.source_hash,
            }
        ]
    }
    result = HandoverContractValidatorV2().validate(
        payload, sources=sources, next_boundary=boundary
    )
    assert adapt_v2_to_legacy_handover_note(result)["open_threads"] == (
        "继续整理相册"
    )


def test_boundary_comes_only_from_outline_and_detects_repeat():
    current = {
        "subsection": 1,
        "title": "相遇",
        "key_points": ["林晚第一次走进面包店"],
    }
    following = {
        "_section": 1,
        "subsection": 2,
        "title": "重复",
        "key_points": ["林晚第一次走进面包店"],
    }
    boundary = compile_next_boundary(
        section=1,
        subsection=1,
        current_outline=current,
        next_outline=following,
    )
    assert boundary.boundary_status == "conflicted"
    assert boundary.allowed_start_events == ("林晚第一次走进面包店",)
    assert boundary.must_not_repeat_events == ("林晚第一次走进面包店",)
    assert boundary.provenance == "deterministic_outline_compilation"
    assert "generated" not in boundary.source_id
    section_end = compile_next_boundary(
        section=1,
        subsection=2,
        current_outline=following,
        next_outline=None,
    )
    assert section_end.next_boundary_unavailable == "section_end"


def test_arc_progress_requires_milestone_source_and_draft_evidence():
    class Event:
        event_id = "event-1"
        source_id = "milestone-1"
        source_hash = hashlib.sha256("里程碑".encode()).hexdigest()
        description = "林晚完成记录"

    text = "林晚完成记录。"
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=text,
        current_outline={"subsection": 1, "title": "记录"},
        next_outline=None,
        arc_milestones=[Event()],
    )
    source = sources["generated-subsection:S1.1"]
    boundary = compile_next_boundary(
        section=1,
        subsection=1,
        current_outline={"subsection": 1, "title": "记录"},
        next_outline=None,
    )
    base = {
        "character_id": "linwan",
        "event_id": "event-1",
        "completion_status": "completed",
        "milestone_source_id": "milestone-1",
        "milestone_source_hash": Event.source_hash,
        "evidence": [_evidence(source, text)],
    }
    accepted = HandoverContractValidatorV2().validate(
        {"arc_progress": [base]}, sources=sources, next_boundary=boundary
    )
    assert adapt_v2_to_legacy_handover_note(accepted)["arc_progress"] == {
        "linwan": "done"
    }
    missing = dict(base)
    missing["milestone_source_id"] = "missing"
    rejected = HandoverContractValidatorV2().validate(
        {"arc_progress": [missing]}, sources=sources, next_boundary=boundary
    )
    assert rejected.rejection_counts == {
        "missing_arc_milestone_source": 1
    }
    assert adapt_v2_to_legacy_handover_note(rejected)["arc_progress"] == {}


def test_contract_and_adapter_are_deterministic():
    _, source, sources, boundary = _fixture()
    payload = {"claims": [_claim(source)]}
    first = HandoverContractValidatorV2().validate(
        payload, sources=sources, next_boundary=boundary
    )
    second = HandoverContractValidatorV2().validate(
        json.loads(json.dumps(payload, ensure_ascii=False)),
        sources=sources,
        next_boundary=boundary,
    )
    assert first.contract.contract_hash == second.contract.contract_hash
    assert adapt_v2_to_legacy_handover_note(first) == (
        adapt_v2_to_legacy_handover_note(second)
    )
    assert all(
        len(evidence.excerpt) <= 140
        for claim in first.contract.end_state.claims
        for evidence in claim.evidence
    )
