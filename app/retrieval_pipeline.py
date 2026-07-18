"""Phase 3 shadow retrieval: bounded query planning and explainable reranking.

This module is deliberately independent from Writer prompt construction.  The
first Phase 3 batch only observes an alternative retrieval result; callers must
continue using the legacy result until the offline gate is explicitly passed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Iterable


SUPPORTED_INTENTS = ("character", "event", "foreshadowing", "scene")
_FORESHADOWING_TERMS = (
    "伏笔", "线索", "秘密", "承诺", "约定", "后续", "真相", "未完成",
    "仍然", "再次", "回收", "铺垫", "暗示", "等待", "将要",
)
_SCENE_TERMS = (
    "房间", "店", "仓库", "书店", "街", "巷", "门口", "操作间", "社区",
    "厨房", "活动室", "医院", "学校", "公司", "凌晨", "清晨", "夜里",
)


def _clean_parts(parts: Iterable[object]) -> list[str]:
    cleaned: list[str] = []
    for part in parts:
        value = str(part or "").strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _text_tokens(text: str) -> set[str]:
    """Return deterministic Chinese bigrams and ASCII words for overlap scoring."""
    lowered = str(text or "").lower()
    tokens = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def _decode_metadata_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(item) for item in decoded] if isinstance(decoded, list) else []


@dataclass(frozen=True)
class PlannedQuery:
    intent: str
    query: str
    characters: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueryPlan:
    current_section: int
    current_subsection: int
    queries: tuple[PlannedQuery, ...]
    max_queries: int

    def to_dict(self) -> dict:
        return {
            "current_section": self.current_section,
            "current_subsection": self.current_subsection,
            "max_queries": self.max_queries,
            "queries": [asdict(query) for query in self.queries],
        }


class QueryPlanner:
    """Build at most four deterministic, intent-specific retrieval queries."""

    def __init__(self, max_queries: int = 4):
        self.max_queries = max(1, min(int(max_queries), len(SUPPORTED_INTENTS)))

    def plan(
        self,
        *,
        topic: str = "",
        section_title: str = "",
        subsection_title: str = "",
        key_points: Iterable[str] = (),
        description: str = "",
        character_names: Iterable[str] = (),
        current_section: int = 0,
        current_subsection: int = 0,
        requested_intents: Iterable[str] | None = None,
    ) -> QueryPlan:
        key_points = _clean_parts(key_points)
        base_parts = _clean_parts(
            [topic, section_title, subsection_title, description, *key_points]
        )
        base_text = " ".join(base_parts)
        if not base_text:
            return QueryPlan(current_section, current_subsection, (), self.max_queries)

        names = tuple(
            name for name in _clean_parts(character_names) if name in base_text
        )
        if requested_intents is None:
            intents = ["event"]
            if names:
                intents.append("character")
            if any(term in base_text for term in _FORESHADOWING_TERMS):
                intents.append("foreshadowing")
            if section_title or subsection_title or any(term in base_text for term in _SCENE_TERMS):
                intents.append("scene")
        else:
            requested = set(_clean_parts(requested_intents))
            intents = [intent for intent in SUPPORTED_INTENTS if intent in requested]

        queries: list[PlannedQuery] = []
        for intent in intents[: self.max_queries]:
            if intent == "character":
                parts = [*names, subsection_title, *key_points, description]
            elif intent == "event":
                parts = [section_title, subsection_title, *key_points, description]
            elif intent == "foreshadowing":
                parts = ["伏笔 线索 承诺 后续", subsection_title, *key_points, description]
            else:
                parts = ["场景 地点 氛围", section_title, subsection_title, description, *key_points]
            query_text = " ".join(_clean_parts(parts))
            if query_text:
                queries.append(PlannedQuery(intent, query_text, names))

        return QueryPlan(
            current_section=current_section,
            current_subsection=current_subsection,
            queries=tuple(queries),
            max_queries=self.max_queries,
        )

    def plan_text(
        self,
        text: str,
        *,
        requested_intents: Iterable[str],
        character_names: Iterable[str] = (),
        current_section: int = 0,
        current_subsection: int = 0,
    ) -> QueryPlan:
        """Plan from a recorded query, used by the fixed offline annotation set."""
        return self.plan(
            description=text,
            character_names=character_names,
            current_section=current_section,
            current_subsection=current_subsection,
            requested_intents=requested_intents,
        )


def merge_candidates(query_results: Iterable[tuple[PlannedQuery, list[dict]]]) -> list[dict]:
    """Merge coarse results by source ID while preserving all matched intents."""
    merged: dict[str, dict] = {}
    for planned_query, items in query_results:
        for item in items:
            source_id = str(item.get("id") or "").strip()
            if not source_id:
                identity = "\0".join(
                    [str(item.get("section", 0)), str(item.get("subsection", 0)), str(item.get("text", ""))]
                )
                source_id = "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
            existing = merged.get(source_id)
            score = float(item.get("score") or 0.0)
            if existing is None:
                existing = dict(item)
                existing["id"] = source_id
                existing["matched_intents"] = []
                existing["matched_queries"] = []
                existing["coarse_ranks"] = []
                existing["best_vector_score"] = score
                existing["best_coarse_rank"] = int(item.get("rank") or 0)
                merged[source_id] = existing
            existing["best_vector_score"] = max(float(existing["best_vector_score"]), score)
            rank = int(item.get("rank") or 0)
            if rank > 0 and (not existing["best_coarse_rank"] or rank < existing["best_coarse_rank"]):
                existing["best_coarse_rank"] = rank
            if planned_query.intent not in existing["matched_intents"]:
                existing["matched_intents"].append(planned_query.intent)
            existing["matched_queries"].append(planned_query.query)
            existing["coarse_ranks"].append(int(item.get("rank") or 0))
    return list(merged.values())


class ExplainableReranker:
    """Rule-only reranker with a complete per-candidate score trace."""

    WEIGHTS = {
        "vector": 0.55,
        "keyword": 0.18,
        "title": 0.10,
        "character": 0.12,
        "chapter_proximity": 0.05,
    }

    def __init__(self, min_score: float = 0.35, max_results: int = 5):
        self.min_score = max(0.0, min(float(min_score), 1.0))
        self.max_results = max(0, min(int(max_results), 5))

    def rerank(self, plan: QueryPlan, candidates: Iterable[dict]) -> dict:
        all_query_text = " ".join(query.query for query in plan.queries)
        query_tokens = _text_tokens(all_query_text)
        query_characters = {
            character for query in plan.queries for character in query.characters
        }
        scored: list[dict] = []

        for candidate in candidates:
            section = int(candidate.get("section") or 0)
            subsection = int(candidate.get("subsection") or 0)
            future = section > plan.current_section > 0
            same_or_future_subsection = (
                plan.current_section > 0
                and plan.current_subsection > 0
                and section == plan.current_section
                and subsection >= plan.current_subsection
            )
            if future or same_or_future_subsection:
                scored.append(self._excluded(candidate, "future_section"))
                continue

            text = str(candidate.get("text", ""))
            title = str(candidate.get("title", ""))
            candidate_tokens = _text_tokens(f"{title} {text}")
            title_tokens = _text_tokens(title)
            keyword_overlap = self._coverage(query_tokens, candidate_tokens)
            title_overlap = self._coverage(query_tokens, title_tokens)

            metadata = candidate.get("metadata") or {}
            candidate_characters = set(_decode_metadata_list(metadata.get("characters")))
            if not candidate_characters:
                candidate_characters = {
                    name for name in query_characters if name in f"{title}{text}"
                }
            character_overlap = (
                len(query_characters & candidate_characters) / len(query_characters)
                if query_characters else 0.0
            )
            if plan.current_section > 0 and 0 < section <= plan.current_section:
                chapter_proximity = 1.0 / (1.0 + plan.current_section - section)
            else:
                chapter_proximity = 0.0
            raw_vector_score = max(
                0.0,
                min(float(candidate.get("best_vector_score") or candidate.get("score") or 0.0), 1.0),
            )
            recorded_ranks = [
                int(rank) for rank in candidate.get("coarse_ranks", []) if int(rank) > 0
            ]
            best_coarse_rank = int(
                candidate.get("best_coarse_rank")
                or (min(recorded_ranks) if recorded_ranks else 0)
                or candidate.get("rank")
                or 0
            )
            rank_vector_score = (
                1.0 / (1.0 + 0.15 * (best_coarse_rank - 1))
                if best_coarse_rank > 0 else 0.0
            )
            # Chroma distance scales vary by embedding/index metric. Keep the
            # raw derived score for audit, but use rank normalization when the
            # absolute score is compressed (for example L2 scores near 0.003).
            vector_score = max(raw_vector_score, rank_vector_score)
            candidate["raw_vector_score"] = round(raw_vector_score, 6)
            candidate["rank_vector_score"] = round(rank_vector_score, 6)
            components = {
                "vector": round(vector_score, 6),
                "keyword": round(keyword_overlap, 6),
                "title": round(title_overlap, 6),
                "character": round(character_overlap, 6),
                "chapter_proximity": round(chapter_proximity, 6),
            }
            base_score = sum(components[name] * weight for name, weight in self.WEIGHTS.items())
            scored.append({
                **candidate,
                "score_components": components,
                "base_score": round(base_score, 6),
                "duplicate_section_penalty": 0.0,
                "final_score": round(base_score, 6),
                "selected": False,
                "reason": "pending_threshold_and_diversity",
            })

        eligible = sorted(
            (item for item in scored if item.get("reason") != "future_section"),
            key=lambda item: (-float(item["base_score"]), str(item.get("id", ""))),
        )
        section_counts: dict[int, int] = {}
        selected: list[dict] = []
        for item in eligible:
            section = int(item.get("section") or 0)
            duplicate_penalty = 0.08 * section_counts.get(section, 0)
            final_score = max(0.0, float(item["base_score"]) - duplicate_penalty)
            item["duplicate_section_penalty"] = round(duplicate_penalty, 6)
            item["final_score"] = round(final_score, 6)
            if final_score < self.min_score:
                item["reason"] = "below_min_score"
                continue
            if len(selected) >= self.max_results:
                item["reason"] = "top_k_limit"
                continue
            item["selected"] = True
            item["reason"] = self._selection_reason(item)
            selected.append(item)
            section_counts[section] = section_counts.get(section, 0) + 1

        return {
            "selected": selected,
            "candidates": sorted(
                scored,
                key=lambda item: (-float(item.get("final_score", 0.0)), str(item.get("id", ""))),
            ),
            "min_score": self.min_score,
            "max_results": self.max_results,
        }

    @staticmethod
    def _coverage(query_tokens: set[str], candidate_tokens: set[str]) -> float:
        return len(query_tokens & candidate_tokens) / len(query_tokens) if query_tokens else 0.0

    @staticmethod
    def _excluded(candidate: dict, reason: str) -> dict:
        return {
            **candidate,
            "score_components": {
                "vector": round(float(candidate.get("best_vector_score") or candidate.get("score") or 0.0), 6),
                "keyword": 0.0,
                "title": 0.0,
                "character": 0.0,
                "chapter_proximity": 0.0,
            },
            "base_score": 0.0,
            "duplicate_section_penalty": 0.0,
            "final_score": 0.0,
            "selected": False,
            "reason": reason,
        }

    @staticmethod
    def _selection_reason(item: dict) -> str:
        components = item["score_components"]
        strongest = max(components, key=components.get)
        intents = ",".join(item.get("matched_intents", [])) or "unknown"
        return f"selected:intents={intents};strongest={strongest}"


class ShadowRetriever:
    """Execute multi-intent coarse recall and return trace-only Phase 3 results."""

    def __init__(self, *, candidate_k: int = 12, min_score: float = 0.35, max_results: int = 5):
        self.candidate_k = max(1, int(candidate_k))
        self.reranker = ExplainableReranker(min_score=min_score, max_results=max_results)

    def run(self, vector_store, plan: QueryPlan, *, task_id: str) -> dict:
        started = time.perf_counter()
        query_results: list[tuple[PlannedQuery, list[dict]]] = []
        coarse_traces: list[dict] = []
        for planned_query in plan.queries:
            items = vector_store.search_with_meta(
                planned_query.query,
                k=self.candidate_k,
                task_id=task_id,
                candidate_k=self.candidate_k,
            )
            query_results.append((planned_query, items))
            coarse_traces.append({
                "intent": planned_query.intent,
                "query": planned_query.query,
                "trace": vector_store.last_search_trace,
            })

        merged = merge_candidates(query_results)
        ranked = self.reranker.rerank(plan, merged)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        selected = ranked["selected"]
        rerank_trace = {
            "min_score": ranked["min_score"],
            "max_results": ranked["max_results"],
            "candidates": [self._candidate_trace(item) for item in ranked["candidates"]],
        }
        return {
            "mode": "shadow",
            "writer_uses": "legacy",
            "plan": plan.to_dict(),
            "filter": {"task_id": task_id},
            "candidate_k_per_query": self.candidate_k,
            "coarse_query_count": len(plan.queries),
            "coarse_traces": coarse_traces,
            "merged_candidate_count": len(merged),
            "selected_count": len(selected),
            "selected_ids": [item.get("id", "") for item in selected],
            "selected_sections": [int(item.get("section") or 0) for item in selected],
            "estimated_context_tokens": math.ceil(sum(len(str(item.get("text", ""))) for item in selected) / 4),
            "elapsed_ms": elapsed_ms,
            "rerank": rerank_trace,
        }

    @staticmethod
    def _candidate_trace(item: dict) -> dict:
        text = str(item.get("text", ""))
        return {
            "id": item.get("id", ""),
            "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "",
            "section": int(item.get("section") or 0),
            "subsection": int(item.get("subsection") or 0),
            "title": str(item.get("title", ""))[:80],
            "matched_intents": list(item.get("matched_intents", [])),
            "coarse_ranks": list(item.get("coarse_ranks", [])),
            "score_components": dict(item.get("score_components", {})),
            "raw_vector_score": item.get("raw_vector_score", 0.0),
            "rank_vector_score": item.get("rank_vector_score", 0.0),
            "base_score": item.get("base_score", 0.0),
            "duplicate_section_penalty": item.get("duplicate_section_penalty", 0.0),
            "final_score": item.get("final_score", 0.0),
            "selected": bool(item.get("selected")),
            "reason": item.get("reason", ""),
        }
