import json

from app.utils.llm_client import estimate_tokens
from app.utils.prompt_templates import HANDOVER_EXTRACTION_PROMPT_V21
from app.writing.handover_contract_v2 import (
    adapt_v2_to_legacy_handover_note,
    build_handover_sources,
    compile_next_boundary,
)
from app.writing.handover_contract_v21 import (
    HANDOVER_COMPACT_V21_MAX_OUTPUT_TOKENS,
    MAX_COMPACT_TEXT,
    build_compact_source_registry,
    compact_payload_metrics,
    render_v21_prompt_context,
    restore_and_validate_v21,
    typical_compact_payload,
    worst_legal_compact_payload,
)


def _fixture():
    text = "林晚回到家。她把照片放进相册。她明确不知道周野的家庭情况。"
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
        "description": "林晚再次到店。",
        "key_points": ["林晚再次到店"],
    }
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=text,
        current_outline=current,
        next_outline=following,
    )
    registry = build_compact_source_registry(sources)
    boundary = compile_next_boundary(
        section=1,
        subsection=1,
        current_outline=current,
        next_outline=following,
    )
    return text, sources, registry, boundary


def _span(text, excerpt):
    start = text.index(excerpt)
    return start, start + len(excerpt)


def test_registry_is_deterministic_and_does_not_expose_hash_in_prompt():
    _, sources, first, _ = _fixture()
    second = build_compact_source_registry(dict(reversed(list(sources.items()))))

    assert first.registry_hash == second.registry_hash
    assert [item.index for item in first.entries] == list(range(len(first.entries)))
    context = render_v21_prompt_context(first)["source_registry"]
    assert all(item.source.source_hash not in context for item in first.entries)
    assert "compiled_boundary" not in HANDOVER_EXTRACTION_PROMPT_V21
    assert "source_hash" not in HANDOVER_EXTRACTION_PROMPT_V21


def test_config_accepts_v21_but_default_remains_v1(monkeypatch):
    from app.config import Settings

    configured = Settings()
    assert configured.WRITER_HANDOVER_CONTRACT_VERSION == "v1"
    monkeypatch.setattr(configured, "WRITER_HANDOVER_CONTRACT_VERSION_RAW", "v2.1")
    monkeypatch.setattr(configured, "WRITER_HANDOVER_CONTRACT_VERSION", "v2.1")
    assert not any(
        "WRITER_HANDOVER_CONTRACT_VERSION" in warning
        for warning in configured.validate()
    )


def test_compact_claim_restores_authoritative_source_hash_span_and_legacy_note():
    text, _, registry, boundary = _fixture()
    start, end = _span(text, "林晚回到家")
    result = restore_and_validate_v21(
        {
            "v": "2.1",
            "s": [[0, start, end, "ls", "c", "c", "林晚|回到|家"]],
            "o": [],
            "f": [],
            "a": [],
        },
        registry=registry,
        next_boundary=boundary,
    )

    claim = result.contract.end_state.claims[0]
    evidence = claim.evidence[0]
    generated = registry.entries[0].source
    assert result.accepted_claim_count == 1
    assert evidence.source_id == generated.source_id
    assert evidence.source_hash == generated.source_hash
    assert evidence.excerpt == text[start:end]
    assert len(evidence.excerpt) <= 140
    assert adapt_v2_to_legacy_handover_note(result)["new_facts"] == ["林晚回到家"]


def test_invalid_index_span_and_text_only_reject_their_items():
    text, _, registry, boundary = _fixture()
    start, end = _span(text, "林晚回到家")
    result = restore_and_validate_v21(
        {
            "v": "2.1",
            "s": [
                [0, start, end, "ls", "c", "c", "林晚|回到|家"],
                [99, start, end, "ls", "c", "c", "无效|引用|来源"],
                [0, end, end, "ls", "c", "c", "空|区间|值"],
                [0, start, end, "ls", "c", "c", "字" * (MAX_COMPACT_TEXT + 1)],
            ],
            "o": [],
            "f": [],
            "a": [],
        },
        registry=registry,
        next_boundary=boundary,
    )

    assert result.accepted_claim_count == 1
    assert result.rejected_claim_count == 3
    assert result.rejection_counts == {"invalid_contract_shape": 3}


def test_explicit_unknown_is_not_promoted_to_confirmed_fact():
    text, _, registry, boundary = _fixture()
    excerpt = "明确不知道周野的家庭情况"
    start, end = _span(text, excerpt)
    result = restore_and_validate_v21(
        {
            "v": "2.1",
            "s": [],
            "o": [],
            "f": [[0, start, end, "kf", "u", "u", "周野|不知道|家庭情况"]],
            "a": [],
        },
        registry=registry,
        next_boundary=boundary,
    )

    assert result.contract.end_state.claims[0].certainty == "explicit_unknown"
    assert adapt_v2_to_legacy_handover_note(result)["new_facts"] == []


def test_open_event_restores_actors_action_and_object_from_compact_semantics():
    text = "林晚等待周野回应邀请。"
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=text,
        current_outline={"subsection": 1, "title": "等待"},
        next_outline=None,
    )
    registry = build_compact_source_registry(sources)
    boundary = compile_next_boundary(
        section=1,
        subsection=1,
        current_outline={"subsection": 1, "title": "等待"},
        next_outline=None,
    )
    result = restore_and_validate_v21(
        {
            "v": "2.1",
            "s": [],
            "o": [[0, 0, len(text) - 1, "o", "林晚,周野|回应|邀请"]],
            "f": [],
            "a": [],
        },
        registry=registry,
        next_boundary=boundary,
    )

    event = result.contract.open_events[0]
    assert event.actors == ("林晚", "周野")
    assert event.action == "回应"
    assert event.object == "邀请"
    assert adapt_v2_to_legacy_handover_note(result)["open_threads"] == "回应邀请"


def test_v21_keeps_psychology_stale_and_unsourced_arc_guards():
    text = "周野把水递给林晚。"
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=text,
        current_outline={"subsection": 1, "title": "递水"},
        next_outline=None,
    )
    registry = build_compact_source_registry(sources)
    boundary = compile_next_boundary(
        section=1,
        subsection=1,
        current_outline={"subsection": 1, "title": "递水"},
        next_outline=None,
    )
    start, end = _span(text, text[:-1])
    payload = {
        "v": "2.1",
        "s": [[0, start, end, "cs", "c", "c", "周野|内心动摇|"]],
        "o": [],
        "f": [[0, start, end, "kf", "p", "c", "周野|把水递给|林晚"]],
        "a": [[1, 0, start, end, "c"]],
    }
    first = restore_and_validate_v21(
        payload, registry=registry, next_boundary=boundary
    )
    assert first.rejection_counts == {
        "invalid_contract_shape": 1,
        "unsupported_psychology": 1,
    }
    accepted = first.contract.end_state.claims[0]
    second = restore_and_validate_v21(
        payload,
        registry=registry,
        next_boundary=boundary,
        stale_completed_claim_hashes=[accepted.claim_hash],
    )
    assert second.rejection_counts == {
        "invalid_contract_shape": 1,
        "unsupported_psychology": 1,
        "stale_completed_event": 1,
    }


def test_payload_capacity_preserves_the_600_token_cap():
    typical = compact_payload_metrics(typical_compact_payload())
    worst = compact_payload_metrics(worst_legal_compact_payload())

    assert HANDOVER_COMPACT_V21_MAX_OUTPUT_TOKENS == 600
    assert typical["estimated_tokens"] <= 350
    assert worst["estimated_tokens"] <= 500
    assert HANDOVER_COMPACT_V21_MAX_OUTPUT_TOKENS - worst["estimated_tokens"] >= 100
    assert estimate_tokens(json.dumps({"v": "2.1"}, ensure_ascii=False)) > 0
