"""Shadow-only whole-item context selection for Phase 4 experiments.

The broker has no Writer dependency and never edits item text.  It is designed
to make selection and budget decisions inspectable while the production Writer
continues to receive its legacy messages.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, replace
from typing import Iterable


REQUIREMENTS = {
    "hard_required",
    "continuity_required",
    "evidence_required",
    "optional_context",
}
PROFILES = {"legacy_full", "continuity_first", "budgeted_broker"}
ACTOR_NAMES = ("林晚", "周野", "季晴", "顾衍", "吴阿姨")
_COMMON = {
    "当前", "小节", "章节", "内容", "写作", "故事", "参考", "已经", "一个", "没有",
    "这个", "那个", "他们", "她们", "自己", "时候", "然后", "还是", "可以", "需要",
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _actors(text: str) -> tuple[str, ...]:
    return tuple(name for name in ACTOR_NAMES if name in (text or ""))


def _terms(text: str) -> set[str]:
    """Extract deterministic event-ish character n-grams, excluding actor names."""
    value = text or ""
    for name in ACTOR_NAMES:
        value = value.replace(name, " ")
    spans = re.findall(r"[\u4e00-\u9fff]{2,}", value)
    terms: set[str] = set()
    for span in spans:
        for size in (2, 3, 4):
            terms.update(span[index:index + size] for index in range(max(0, len(span) - size + 1)))
    return {term for term in terms if term not in _COMMON}


@dataclass(frozen=True)
class ContextItem:
    item_id: str
    source_id: str
    source_type: str
    requirement: str
    priority: str
    text: str
    estimated_tokens: int
    injection_position: str
    section: int | None = None
    subsection: int | None = None
    actors: tuple[str, ...] = ()
    provenance: str = "legacy_prompt_block"
    characters: int | None = None
    text_hash: str | None = None
    keep: bool | None = None
    keep_reason: str | None = None
    drop_reason: str | None = None
    budget_before: int | None = None
    budget_after: int | None = None
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        if self.requirement not in REQUIREMENTS:
            raise ValueError(f"invalid requirement: {self.requirement}")
        if self.priority not in {"P0", "P1", "P2", "P3"}:
            raise ValueError(f"invalid priority: {self.priority}")
        if not self.item_id or not self.source_id:
            raise ValueError("item_id and source_id are required")
        object.__setattr__(self, "characters", len(self.text) if self.characters is None else self.characters)
        object.__setattr__(self, "text_hash", _sha256(self.text) if self.text_hash is None else self.text_hash)
        object.__setattr__(self, "actors", self.actors or _actors(self.text))

    def trace(self) -> dict:
        """Serialize decision metadata without copying source text into reports."""
        return {
            "item_id": self.item_id,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "requirement": self.requirement,
            "priority": self.priority,
            "text_hash": self.text_hash,
            "characters": self.characters,
            "estimated_tokens": self.estimated_tokens,
            "injection_position": self.injection_position,
            "section": self.section,
            "subsection": self.subsection,
            "actors": list(self.actors),
            "keep": self.keep,
            "keep_reason": self.keep_reason,
            "drop_reason": self.drop_reason,
            "budget_before": self.budget_before,
            "budget_after": self.budget_after,
            "fallback_reason": self.fallback_reason,
            "provenance": self.provenance,
        }


def priority_for(source_type: str, *, immediate_previous: bool = False, locked: bool = False) -> tuple[str, str]:
    if source_type in {"fixed_prompt", "current_writing", "character_relation"} or locked:
        return "hard_required", "P0"
    if source_type == "handover" or (source_type == "recent_original" and immediate_previous):
        return "continuity_required", "P1"
    if source_type == "rag":
        return "evidence_required", "P2"
    return "optional_context", "P3"


def older_recent_relevance(item: ContextItem, *, query: str, immediate_text: str) -> dict:
    query_actors = set(_actors(query))
    item_actors = set(item.actors)
    immediate_actors = set(_actors(immediate_text))
    query_terms = _terms(query)
    item_terms = _terms(item.text)
    immediate_terms = _terms(immediate_text)
    event_hits = sorted(query_terms & item_terms)
    continuity_hits = sorted(immediate_terms & item_terms)
    unique_actors = sorted((query_actors & item_actors) - immediate_actors)
    event_ratio = len(event_hits) / max(1, len(query_terms))
    continuity_ratio = len(continuity_hits) / max(1, min(len(immediate_terms), 80))
    if unique_actors or len(event_hits) >= 3 or event_ratio >= 0.08 or len(continuity_hits) >= 5:
        decision, reason, fallback = True, "older_recent_relevant_signal", None
    elif not event_hits and not continuity_hits and not (query_actors & item_actors):
        decision, reason, fallback = False, "older_recent_clear_nonmatch", None
    else:
        decision, reason, fallback = True, "older_recent_ambiguous_keep", "insufficient_signal_to_drop_safely"
    return {
        "keep": decision,
        "reason": reason,
        "fallback_reason": fallback,
        "signals": {
            "actor_intersection": sorted(query_actors & item_actors),
            "unique_actor_intersection": unique_actors,
            "event_term_hits": event_hits[:20],
            "continuity_term_hits": continuity_hits[:20],
            "event_ratio": round(event_ratio, 4),
            "continuity_ratio": round(continuity_ratio, 4),
        },
    }


class ContextBroker:
    """Select complete ContextItems under a soft budget."""

    def __init__(self, target_tokens: int = 8500) -> None:
        self.target_tokens = target_tokens

    def select(self, items: Iterable[ContextItem], *, profile: str, query: str) -> dict:
        if profile not in PROFILES:
            raise ValueError(f"unknown broker profile: {profile}")
        started = time.perf_counter()
        source = list(items)
        if len({item.item_id for item in source}) != len(source):
            raise ValueError("ContextItem IDs must be unique")
        immediate = next(
            (item for item in source if item.source_type == "recent_original" and item.priority == "P1"),
            None,
        )
        relevance: dict[str, dict] = {}
        for item in source:
            if item.source_type == "recent_original" and item.priority == "P3":
                relevance[item.item_id] = older_recent_relevance(
                    item, query=query, immediate_text=immediate.text if immediate else ""
                )

        if profile == "legacy_full":
            selected = self._legacy(source)
            overflow = None
        elif profile == "continuity_first":
            selected = self._continuity(source, relevance)
            overflow = None
        else:
            selected, overflow = self._budgeted(source, relevance)
        return {
            "profile": profile,
            "target_tokens": self.target_tokens if profile == "budgeted_broker" else None,
            "total_estimated_tokens": sum(item.estimated_tokens for item in selected if item.keep),
            "kept_item_count": sum(bool(item.keep) for item in selected),
            "dropped_item_count": sum(item.keep is False for item in selected),
            "budget_overflow_reason": overflow,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "older_recent_signals": relevance,
            "items": [item.trace() for item in selected],
        }

    @staticmethod
    def _legacy(items: list[ContextItem]) -> list[ContextItem]:
        running = 0
        result = []
        for item in items:
            before = running
            running += item.estimated_tokens
            result.append(replace(item, keep=True, keep_reason="legacy_full", budget_before=before, budget_after=running))
        return result

    @staticmethod
    def _continuity(items: list[ContextItem], relevance: dict[str, dict]) -> list[ContextItem]:
        running = 0
        result = []
        for item in items:
            signal = relevance.get(item.item_id)
            keep = signal["keep"] if signal else True
            before = running
            after = running + item.estimated_tokens if keep else running
            result.append(replace(
                item,
                keep=keep,
                keep_reason=(signal["reason"] if signal and keep else "continuity_first_default_keep") if keep else None,
                drop_reason=signal["reason"] if signal and not keep else None,
                fallback_reason=signal.get("fallback_reason") if signal else None,
                budget_before=before,
                budget_after=after,
            ))
            running = after
        return result

    def _budgeted(self, items: list[ContextItem], relevance: dict[str, dict]) -> tuple[list[ContextItem], str | None]:
        protected = [item for item in items if item.priority in {"P0", "P1", "P2"}]
        optional = [item for item in items if item.priority == "P3"]
        decisions: dict[str, ContextItem] = {}
        running = 0
        for item in protected:
            before = running
            running += item.estimated_tokens
            decisions[item.item_id] = replace(
                item, keep=True, keep_reason=f"protected_{item.priority}", budget_before=before, budget_after=running
            )
        optional_order = {"recent_original": 0, "other": 1, "style_examples": 2, "world_event": 3}
        optional.sort(key=lambda item: (optional_order.get(item.source_type, 4), item.item_id))
        for item in optional:
            signal = relevance.get(item.item_id)
            eligible = signal["keep"] if signal else True
            before = running
            if not eligible:
                decisions[item.item_id] = replace(
                    item, keep=False, drop_reason=signal["reason"], budget_before=before,
                    budget_after=before, fallback_reason=signal.get("fallback_reason"),
                )
            elif running + item.estimated_tokens <= self.target_tokens:
                running += item.estimated_tokens
                decisions[item.item_id] = replace(
                    item, keep=True, keep_reason=signal["reason"] if signal else "optional_fits_budget",
                    budget_before=before, budget_after=running,
                    fallback_reason=signal.get("fallback_reason") if signal else None,
                )
            else:
                decisions[item.item_id] = replace(
                    item, keep=False, drop_reason="soft_budget_would_be_exceeded",
                    budget_before=before, budget_after=before,
                    fallback_reason=signal.get("fallback_reason") if signal else None,
                )
        overflow = None
        if sum(item.estimated_tokens for item in protected) > self.target_tokens:
            overflow = "protected_P0_P1_P2_exceed_soft_budget"
        return [decisions[item.item_id] for item in items], overflow
