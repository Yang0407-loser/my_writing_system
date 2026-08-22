"""Deterministic Writer-context accounting used by the Phase 4 entry audit.

This module is deliberately not connected to Writer.  It classifies already
assembled prompt blocks, reconciles them to the project's current token
estimator and diagnoses only textually provable overlap.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable


SOURCE_CONTRACTS = {
    "fixed_prompt": {
        "producer": "app.utils.prompt_templates",
        "storage": "source code",
        "injection": "Writer system message and WRITING_PROMPT scaffold",
        "requirement": "hard_required",
        "trimmable": False,
    },
    "current_writing": {
        "producer": "outline/current subsection + Writer helpers",
        "storage": "task state / frozen query",
        "injection": "hard constraints, progress and current-outline fields",
        "requirement": "hard_required",
        "trimmable": False,
    },
    "recent_original": {
        "producer": "ContextManager",
        "storage": "checkpoint buffer (most recent three subsection texts)",
        "injection": "reference information / summary_context",
        "requirement": "continuity_required",
        "trimmable": True,
    },
    "handover": {
        "producer": "Writer handover extraction",
        "storage": "handover chain",
        "injection": "previous-section handover_context",
        "requirement": "continuity_required",
        "trimmable": True,
    },
    "character_relation": {
        "producer": "character cards, arcs, constraints and relation store",
        "storage": "task state / rule and relation stores",
        "injection": "hard constraints, rules, character and arc fields",
        "requirement": "hard_required",
        "trimmable": False,
    },
    "world_event": {
        "producer": "world state, EventGraph and world-element stores",
        "storage": "task state / state stores",
        "injection": "world facts, contradictions and ranked events",
        "requirement": "optional_context",
        "trimmable": True,
    },
    "rag": {
        "producer": "legacy VectorStore.search_with_meta",
        "storage": "shared Chroma collection filtered by task_id",
        "injection": "reference information / retrieved_context",
        "requirement": "evidence_required",
        "trimmable": True,
    },
    "style_examples": {
        "producer": "four-control style contract and optional references",
        "storage": "task style state / deterministic templates",
        "injection": "style fields and style_examples",
        "requirement": "optional_context",
        "trimmable": True,
    },
    "other": {
        "producer": "optional Writer inputs",
        "storage": "task state and auxiliary stores",
        "injection": "rules_context or dedicated template fields",
        "requirement": "optional_context",
        "trimmable": True,
    },
}


def estimate_tokens(text: str) -> int:
    """Mirror Writer's stable local estimate (not a model tokenizer)."""
    if not text:
        return 0
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - chinese
    return int(chinese * 1.5 + other * 0.3)


def normalize_text(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", (text or "").lower())


def sentence_set(text: str) -> set[str]:
    return {
        normalize_text(part)
        for part in re.split(r"[。！？!?；;\n]+", text or "")
        if len(normalize_text(part)) >= 8
    }


def make_block(
    block_id: str,
    category: str,
    text: str,
    *,
    source_id: str,
    injection_position: str,
    requirement: str | None = None,
    available: bool = True,
) -> dict:
    if category not in SOURCE_CONTRACTS:
        raise ValueError(f"unknown context category: {category}")
    contract = SOURCE_CONTRACTS[category]
    return {
        "block_id": block_id,
        "category": category,
        "text": text or "",
        "characters": len(text or ""),
        "estimated_tokens": estimate_tokens(text or ""),
        "source_id": source_id,
        "injection_position": injection_position,
        "requirement": requirement or contract["requirement"],
        "trimmable": bool(contract["trimmable"]),
        "available": bool(available),
    }


def build_ledger(blocks: Iterable[dict], total_prompt_tokens: int) -> dict:
    items = list(blocks)
    category_tokens: dict[str, int] = defaultdict(int)
    category_characters: dict[str, int] = defaultdict(int)
    category_items: dict[str, int] = defaultdict(int)
    for block in items:
        category = block["category"]
        category_tokens[category] += int(block["estimated_tokens"])
        category_characters[category] += int(block["characters"])
        category_items[category] += 1
    accounted = sum(category_tokens.values())
    delta = int(total_prompt_tokens) - accounted
    category_tokens["fixed_prompt"] += delta
    return {
        "total_estimated_tokens": int(total_prompt_tokens),
        "accounted_before_reconciliation": accounted,
        "reconciliation_delta_assigned_to_fixed_prompt": delta,
        "categories": {
            category: {
                "estimated_tokens": category_tokens.get(category, 0),
                "characters": category_characters.get(category, 0),
                "items": category_items.get(category, 0),
                "share": round(category_tokens.get(category, 0) / total_prompt_tokens, 4)
                if total_prompt_tokens else 0.0,
            }
            for category in SOURCE_CONTRACTS
        },
    }


def diagnose_duplicates(blocks: Iterable[dict], jaccard_threshold: float = 0.75) -> dict:
    items = [item for item in blocks if item.get("text") and item.get("available", True)]
    pairs = []
    provable_drop_candidates: dict[str, int] = {}
    for index, left in enumerate(items):
        left_norm = normalize_text(left["text"])
        if len(left_norm) < 16:
            continue
        for right in items[index + 1:]:
            if left["category"] == right["category"] and left["block_id"] == right["block_id"]:
                continue
            right_norm = normalize_text(right["text"])
            if len(right_norm) < 16:
                continue
            relation = ""
            similarity = 0.0
            drop = None
            if left_norm == right_norm:
                relation, similarity = "exact", 1.0
                drop = min((left, right), key=lambda item: item["estimated_tokens"])
            elif left_norm in right_norm or right_norm in left_norm:
                relation, similarity = "containment", round(
                    min(len(left_norm), len(right_norm)) / max(len(left_norm), len(right_norm)), 4
                )
                drop = left if len(left_norm) < len(right_norm) else right
            else:
                left_sentences = sentence_set(left["text"])
                right_sentences = sentence_set(right["text"])
                union = left_sentences | right_sentences
                similarity = len(left_sentences & right_sentences) / len(union) if union else 0.0
                if similarity >= jaccard_threshold:
                    relation = "sentence_jaccard"
            if not relation:
                continue
            pair = {
                "left_block_id": left["block_id"],
                "left_category": left["category"],
                "right_block_id": right["block_id"],
                "right_category": right["category"],
                "relation": relation,
                "similarity": round(similarity, 4),
                "provably_removable_block_id": drop["block_id"] if drop else None,
            }
            pairs.append(pair)
            if drop and drop.get("trimmable"):
                provable_drop_candidates[drop["block_id"]] = int(drop["estimated_tokens"])
    return {
        "pair_count": len(pairs),
        "pairs": pairs,
        "provable_duplicate_tokens": sum(provable_drop_candidates.values()),
        "provable_drop_block_ids": sorted(provable_drop_candidates),
        "jaccard_only_pairs_not_counted_as_savings": sum(
            pair["relation"] == "sentence_jaccard" for pair in pairs
        ),
    }


def validate_required_manifest(items: Iterable[dict]) -> None:
    allowed = {"hard_required", "continuity_required", "evidence_required", "optional_context"}
    seen = set()
    for item in items:
        item_id = str(item.get("item_id", "")).strip()
        if not item_id or item_id in seen:
            raise ValueError("required manifest item IDs must be non-empty and unique")
        seen.add(item_id)
        if item.get("requirement") not in allowed:
            raise ValueError(f"invalid requirement: {item.get('requirement')}")
        if not str(item.get("source_id", "")).strip():
            raise ValueError(f"manifest item {item_id} has no source_id")
