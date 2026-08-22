import pytest

from app.agents.writer import _estimate_prompt_tokens
from app.context_census import (
    build_ledger,
    diagnose_duplicates,
    estimate_tokens,
    make_block,
    validate_required_manifest,
)


def test_estimator_matches_writer_contract():
    text = "最近三小节 original text 123。"
    assert estimate_tokens(text) == _estimate_prompt_tokens(text)


def test_ledger_reconciles_exactly_to_rendered_prompt_total():
    blocks = [
        make_block("fixed", "fixed_prompt", "固定", source_id="prompt", injection_position="system"),
        make_block("recent", "recent_original", "最近正文", source_id="s1", injection_position="summary"),
    ]
    ledger = build_ledger(blocks, 20)
    assert sum(item["estimated_tokens"] for item in ledger["categories"].values()) == 20
    assert ledger["categories"]["recent_original"]["items"] == 1


def test_duplicate_diagnostic_counts_only_provable_trimmable_copy():
    original = make_block(
        "recent", "recent_original", "林晚推开面包店的门。凌晨三点半，周野正在木制案板前反复揉面。",
        source_id="recent", injection_position="summary",
    )
    duplicate = make_block(
        "rag", "rag", "凌晨三点半，周野正在木制案板前反复揉面。",
        source_id="rag", injection_position="retrieved",
    )
    result = diagnose_duplicates([original, duplicate])
    assert result["pair_count"] == 1
    assert result["pairs"][0]["relation"] == "containment"
    assert result["provable_drop_block_ids"] == ["rag"]
    assert result["provable_duplicate_tokens"] == duplicate["estimated_tokens"]


def test_low_overlap_is_not_called_noise():
    left = make_block("a", "rag", "林晚推开面包店的门。", source_id="a", injection_position="rag")
    right = make_block("b", "handover", "季晴核对借款风险。", source_id="b", injection_position="handover")
    assert diagnose_duplicates([left, right])["pair_count"] == 0


def test_required_manifest_contract():
    validate_required_manifest([
        {"item_id": "goal", "requirement": "hard_required", "source_id": "outline"},
        {"item_id": "previous", "requirement": "continuity_required", "source_id": "S1:U1"},
        {"item_id": "fact", "requirement": "evidence_required", "source_id": "chunk-1"},
        {"item_id": "style", "requirement": "optional_context", "source_id": "style"},
    ])


def test_required_manifest_rejects_duplicate_or_unknown_requirement():
    with pytest.raises(ValueError):
        validate_required_manifest([
            {"item_id": "same", "requirement": "hard_required", "source_id": "a"},
            {"item_id": "same", "requirement": "optional_context", "source_id": "b"},
        ])
    with pytest.raises(ValueError):
        validate_required_manifest([
            {"item_id": "bad", "requirement": "maybe", "source_id": "a"},
        ])
