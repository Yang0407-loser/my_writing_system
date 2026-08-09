"""WR4 tuning components: planner and reranker variants (experiment only).

All components are deterministic and offline.  They intentionally do NOT
modify ``app.retrieval_pipeline``; Writer continues to consume the legacy
retrieval path.  Query construction uses only the writing requirement text
(never the gold facts), so the ablation stays free of gold leakage.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict
from typing import Any, Callable, Iterable

from app.retrieval_pipeline import (
    SUPPORTED_INTENTS,
    PlannedQuery,
    QueryPlan,
    QueryPlanner,
    QueryPlannerV2,
    _clean_parts,
    _decode_metadata_list,
    _text_tokens,
)

CHARACTER_NAMES = ("林晚", "周野", "季晴", "顾衍", "吴阿姨")


ACTION_TERMS = (
    "说", "问", "告诉", "发现", "知道", "决定", "答应", "拒绝", "删除", "删帖",
    "拍", "记录", "写", "回忆", "想起", "加入", "离开", "回来", "帮助", "邀请",
    "承诺", "等待", "寻找", "收到", "递", "揉面", "做", "交代", "联系", "讨论",
)
SCENE_TERMS = (
    "房间", "店", "仓库", "书店", "街", "巷", "门口", "操作间", "社区",
    "厨房", "活动室", "医院", "学校", "公司", "凌晨", "清晨", "夜里",
)
FORESHADOWING_TERMS = (
    "伏笔", "线索", "秘密", "承诺", "约定", "后续", "真相", "未完成",
    "仍然", "再次", "回收", "铺垫", "暗示", "等待", "将要",
)
STATE_TERMS = (
    "几点", "时间", "状态", "位置", "是否", "知道", "营业", "开门", "关门",
    "星期", "职业", "身份", "现在", "当前", "知情",
)

LOCATION_LEXICON: dict[str, str] = {
    "操作间": "操作间",
    "合租房": "合租房",
    "面包店": "野面包店",
    "书店": "书店",
    "社区活动室": "社区活动室",
    "工坊": "新工坊",
    "仓库": "旧仓库",
    "医院": "医院",
    "厨房": "厨房",
    "公司": "公司",
    "学校": "学校",
    "门口": "店门口",
}


def _clauses(text: str) -> list[str]:
    parts = re.split(r"[\s，。；！？：:、]+", str(text or ""))
    return [part.strip() for part in parts if len(part.strip()) >= 2]


def _anchored_clauses(clauses: list[str], names: tuple[str, ...]) -> list[str]:
    anchored: list[str] = []
    for clause in clauses:
        has_name = any(name in clause for name in names)
        has_action = any(term in clause for term in ACTION_TERMS)
        if has_action or (has_name and len(clause) >= 6):
            if clause not in anchored:
                anchored.append(clause)
    return anchored


class QueryPlannerWR4:
    """Rich planner: V1 full-text intents + V2 anchored clauses + event fallback.

    Never reads gold facts; it only sees the writing requirement text, the
    requested intents, character names and the current section/subsection.
    """

    def __init__(self, max_queries: int = 4):
        self.max_queries = max(1, min(int(max_queries), 4))

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
        requested = (
            set(_clean_parts(requested_intents))
            if requested_intents is not None
            else set(SUPPORTED_INTENTS)
        )
        clauses = _clauses(base_text)
        anchored = _anchored_clauses(clauses, names)
        queries: list[PlannedQuery] = []

        # V1 full-text per requested intent.
        intents = [intent for intent in SUPPORTED_INTENTS if intent in requested]
        # Character/scene-only requirements still need an event-style anchor.
        add_event_fallback = "event" not in requested and bool(anchored)
        for intent in intents:
            if intent == "character":
                character_clauses = [
                    clause for clause in anchored if any(name in clause for name in names)
                ]
                parts = [*names, *character_clauses[-2:], description]
            elif intent == "event":
                parts = [*anchored[-3:], *key_points[-2:], description]
            elif intent == "foreshadowing":
                clue_parts = [
                    clause for clause in clauses
                    if any(term in clause for term in FORESHADOWING_TERMS)
                ]
                parts = ["伏笔 后续", *clue_parts[-2:], description]
            else:
                explicit_scene = [
                    clause for clause in clauses
                    if any(term in clause for term in SCENE_TERMS)
                ]
                parts = ["场景 地点 氛围", *explicit_scene[-2:], description]
            query_text = " ".join(_clean_parts(parts))
            if query_text and query_text not in {query.query for query in queries}:
                queries.append(PlannedQuery(intent, query_text, names))

        # Anchored event fallback (V2 style).
        if add_event_fallback and anchored:
            event_text = " ".join(_clean_parts([*anchored[-3:], *key_points[-2:]]))
            if event_text and event_text not in {query.query for query in queries}:
                queries.append(PlannedQuery("event", event_text, names))

        # State/fact query when the requirement asks about a current state.
        if any(term in base_text for term in STATE_TERMS) and anchored:
            state_text = " ".join(_clean_parts(anchored[-2:]))
            if state_text and state_text not in {query.query for query in queries}:
                queries.append(PlannedQuery("event", state_text, names))

        return QueryPlan(
            current_section=current_section,
            current_subsection=current_subsection,
            queries=tuple(queries[: self.max_queries]),
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
        return self.plan(
            description=text,
            character_names=character_names,
            current_section=current_section,
            current_subsection=current_subsection,
            requested_intents=requested_intents,
        )


class QueryPlannerHybrid:
    """Union of V1 and V2 query sets, deduplicated and capped at four."""

    def __init__(self, max_queries: int = 4):
        self.max_queries = max(1, min(int(max_queries), 4))

    def plan_text(
        self,
        text: str,
        *,
        requested_intents: Iterable[str],
        character_names: Iterable[str] = (),
        current_section: int = 0,
        current_subsection: int = 0,
    ) -> QueryPlan:
        v1 = QueryPlanner(max_queries=4).plan_text(
            text,
            requested_intents=requested_intents,
            character_names=character_names,
            current_section=current_section,
            current_subsection=current_subsection,
        )
        v2 = QueryPlannerV2(max_queries=2).plan(
            description=text,
            character_names=character_names,
            current_section=current_section,
            current_subsection=current_subsection,
            requested_intents=requested_intents,
        )
        queries: list[PlannedQuery] = []
        for query in [*v1.queries, *v2.queries]:
            if query.query not in {existing.query for existing in queries}:
                queries.append(query)
        if not any(query.intent == "event" for query in queries) and any(
            query.intent != "event" for query in queries
        ):
            clauses = _clauses(text)
            anchored = _anchored_clauses(clauses, tuple(character_names))
            if anchored:
                queries.append(
                    PlannedQuery("event", " ".join(anchored[-2:]), tuple(character_names))
                )
        return QueryPlan(
            current_section=current_section,
            current_subsection=current_subsection,
            queries=tuple(queries[: self.max_queries]),
            max_queries=self.max_queries,
        )


class QueryPlannerV2Adapter(QueryPlannerV2):
    """Uniform ``plan_text`` entry for the V2 planner."""

    def plan_text(
        self,
        text: str,
        *,
        requested_intents: Iterable[str],
        character_names: Iterable[str] = (),
        current_section: int = 0,
        current_subsection: int = 0,
    ) -> QueryPlan:
        return self.plan(
            description=text,
            character_names=character_names,
            current_section=current_section,
            current_subsection=current_subsection,
            requested_intents=requested_intents,
        )


PLANNER_REGISTRY: dict[str, Callable[[], Any]] = {
    "v1": lambda: QueryPlanner(max_queries=4),
    "v2": lambda: QueryPlannerV2Adapter(max_queries=2),
    "wr4_rich": lambda: QueryPlannerWR4(max_queries=4),
    "wr4_hybrid": lambda: QueryPlannerHybrid(max_queries=4),
}


# ---------------------------------------------------------------------------
# Parameterized reranker (standalone mirror of the shadow reranker logic).
# ---------------------------------------------------------------------------


def _excluded(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    text = str(candidate.get("text", ""))
    return {
        **candidate,
        "score_components": {
            "vector": round(
                float(candidate.get("best_vector_score") or candidate.get("score") or 0.0),
                6,
            ),
            "keyword": 0.0,
            "title": 0.0,
            "character": 0.0,
            "chapter_proximity": 0.0,
        },
        "base_score": 0.0,
        "score_without_character": 0.0,
        "character_evidence": {"mode": "excluded"},
        "estimated_tokens": math.ceil(len(text) / 4),
        "duplicate_section_penalty": 0.0,
        "final_score": 0.0,
        "selected": False,
        "reason": reason,
    }


def _character_score(
    *,
    mode: str,
    query_characters: set[str],
    title: str,
    text: str,
    metadata: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    if not query_characters:
        return 0.0, {"mode": mode, "metadata_matches": []}
    metadata_names = set(_decode_metadata_list(metadata.get("characters")))
    if mode == "graded":
        metadata_matches = query_characters & metadata_names
        title_matches = {name for name in query_characters if name in title}
        text_mentions = {name: text.count(name) for name in query_characters}
        metadata_coverage = len(metadata_matches) / len(query_characters)
        title_coverage = len(title_matches) / len(query_characters)
        text_strength = sum(
            1.0 if count >= 2 else 0.5 if count == 1 else 0.0
            for count in text_mentions.values()
        ) / len(query_characters)
        score = 0.45 * metadata_coverage + 0.25 * title_coverage + 0.30 * text_strength
        return score, {
            "mode": mode,
            "metadata_matches": sorted(metadata_matches),
            "title_matches": sorted(title_matches),
            "text_mentions": dict(sorted(text_mentions.items())),
            "metadata_coverage": round(metadata_coverage, 6),
            "title_coverage": round(title_coverage, 6),
            "text_strength": round(text_strength, 6),
        }
    candidate_characters = metadata_names or {
        name for name in query_characters if name in f"{title}{text}"
    }
    overlap = (
        len(query_characters & candidate_characters) / len(query_characters)
        if query_characters
        else 0.0
    )
    return overlap, {
        "mode": mode,
        "metadata_matches": sorted(query_characters & candidate_characters),
    }


def rerank_candidates(
    plan: QueryPlan,
    candidates: Iterable[dict[str, Any]],
    *,
    weights: dict[str, float],
    min_score: float = 0.35,
    max_results: int = 5,
    token_budget: int | None = None,
    require_non_character_support: bool = False,
    duplicate_penalty: float = 0.08,
    character_mode: str = "binary",
) -> dict[str, Any]:
    """Deterministic rerank mirror with tunable weights and gates."""
    all_query_text = " ".join(query.query for query in plan.queries)
    query_tokens = _text_tokens(all_query_text)
    query_characters = {
        character for query in plan.queries for character in query.characters
    }
    query_locations = {
        canonical
        for term, canonical in LOCATION_LEXICON.items()
        if term in all_query_text
    }
    scored: list[dict[str, Any]] = []
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
            scored.append(_excluded(candidate, "future_section"))
            continue

        text = str(candidate.get("text", ""))
        title = str(candidate.get("title", ""))
        candidate_tokens = _text_tokens(f"{title} {text}")
        keyword_overlap = (
            len(query_tokens & candidate_tokens) / len(query_tokens)
            if query_tokens
            else 0.0
        )
        title_overlap = (
            len(query_tokens & _text_tokens(title)) / len(query_tokens)
            if query_tokens
            else 0.0
        )
        character_overlap, character_evidence = _character_score(
            mode=character_mode,
            query_characters=query_characters,
            title=title,
            text=text,
            metadata=candidate.get("metadata") or {},
        )
        metadata_names = set(_decode_metadata_list((candidate.get("metadata") or {}).get("characters")))
        metadata_locations = set(
            _decode_metadata_list((candidate.get("metadata") or {}).get("locations"))
        )
        evidence_terms = query_characters | query_locations
        metadata_overlap = (
            len((query_characters & metadata_names) | (query_locations & metadata_locations))
            / len(evidence_terms)
            if evidence_terms
            else 0.0
        )
        if plan.current_section > 0 and 0 < section <= plan.current_section:
            chapter_proximity = 1.0 / (1.0 + plan.current_section - section)
        else:
            chapter_proximity = 0.0
        raw_vector_score = max(
            0.0,
            min(
                float(
                    candidate.get("best_vector_score")
                    or candidate.get("score")
                    or 0.0
                ),
                1.0,
            ),
        )
        recorded_ranks = [
            int(rank)
            for rank in candidate.get("coarse_ranks", [])
            if int(rank) > 0
        ]
        best_coarse_rank = int(
            candidate.get("best_coarse_rank")
            or (min(recorded_ranks) if recorded_ranks else 0)
            or candidate.get("rank")
            or 0
        )
        rank_vector_score = (
            1.0 / (1.0 + 0.15 * (best_coarse_rank - 1))
            if best_coarse_rank > 0
            else 0.0
        )
        vector_score = max(raw_vector_score, rank_vector_score)
        components = {
            "vector": round(vector_score, 6),
            "keyword": round(keyword_overlap, 6),
            "title": round(title_overlap, 6),
            "character": round(character_overlap, 6),
            "chapter_proximity": round(chapter_proximity, 6),
            "metadata_evidence": round(metadata_overlap, 6),
        }
        base_score = sum(
            components[name] * weight for name, weight in weights.items()
        )
        score_without_character = (
            base_score - components["character"] * weights.get("character", 0.0)
        )
        scored.append(
            {
                **candidate,
                "score_components": components,
                "character_evidence": character_evidence,
                "base_score": round(base_score, 6),
                "score_without_character": round(score_without_character, 6),
                "estimated_tokens": math.ceil(len(text) / 4),
                "duplicate_section_penalty": 0.0,
                "final_score": round(base_score, 6),
                "selected": False,
                "reason": "pending_threshold_and_diversity",
            }
        )

    eligible = sorted(
        (item for item in scored if item.get("reason") != "future_section"),
        key=lambda item: (-float(item["base_score"]), str(item.get("id", ""))),
    )
    section_counts: dict[int, int] = {}
    selected: list[dict[str, Any]] = []
    used_tokens = 0
    for item in eligible:
        section = int(item.get("section") or 0)
        penalty = duplicate_penalty * section_counts.get(section, 0)
        final_score = max(0.0, float(item["base_score"]) - penalty)
        item["duplicate_section_penalty"] = round(penalty, 6)
        item["final_score"] = round(final_score, 6)
        if final_score < min_score:
            item["reason"] = "below_min_score"
            continue
        if require_non_character_support and float(item.get("score_without_character", 0.0)) < min_score:
            item["reason"] = "below_non_character_support"
            continue
        if len(selected) >= max_results:
            item["reason"] = "top_k_limit"
            continue
        item_tokens = int(item.get("estimated_tokens", 0))
        if token_budget is not None and used_tokens + item_tokens > token_budget:
            item["reason"] = "token_budget_limit"
            continue
        item["selected"] = True
        component_map = item.get("score_components") or {}
        strongest = max(component_map, key=component_map.get) if component_map else "vector"
        item["reason"] = (
            f"selected:intents={','.join(item.get('matched_intents', [])) or 'unknown'};"
            f"strongest={strongest}"
        )
        selected.append(item)
        used_tokens += item_tokens
        section_counts[section] = section_counts.get(section, 0) + 1

    return {
        "selected": selected,
        "candidates": scored,
        "min_score": min_score,
        "max_results": max_results,
        "token_budget": token_budget,
    }


RERANKER_REGISTRY: dict[str, dict[str, Any]] = {
    "v1_035": {
        "weights": {
            "vector": 0.55,
            "keyword": 0.18,
            "title": 0.10,
            "character": 0.12,
            "chapter_proximity": 0.05,
        },
        "min_score": 0.35,
        "require_non_character_support": False,
        "duplicate_penalty": 0.08,
        "character_mode": "binary",
    },
    "v1_025": {
        "weights": {
            "vector": 0.55,
            "keyword": 0.18,
            "title": 0.10,
            "character": 0.12,
            "chapter_proximity": 0.05,
        },
        "min_score": 0.25,
        "require_non_character_support": False,
        "duplicate_penalty": 0.08,
        "character_mode": "binary",
    },
    "v1_020": {
        "weights": {
            "vector": 0.55,
            "keyword": 0.18,
            "title": 0.10,
            "character": 0.12,
            "chapter_proximity": 0.05,
        },
        "min_score": 0.20,
        "require_non_character_support": False,
        "duplicate_penalty": 0.08,
        "character_mode": "binary",
    },
    "v2_025": {
        "weights": {
            "vector": 0.55,
            "keyword": 0.18,
            "title": 0.10,
            "character": 0.12,
            "chapter_proximity": 0.05,
        },
        "min_score": 0.25,
        "require_non_character_support": True,
        "duplicate_penalty": 0.08,
        "character_mode": "graded",
        "token_budget": 600,
    },
    "vec_heavy_030": {
        "weights": {
            "vector": 0.70,
            "keyword": 0.12,
            "title": 0.08,
            "character": 0.06,
            "chapter_proximity": 0.04,
        },
        "min_score": 0.30,
        "require_non_character_support": False,
        "duplicate_penalty": 0.08,
        "character_mode": "binary",
    },
    "balanced_025": {
        "weights": {
            "vector": 0.60,
            "keyword": 0.15,
            "title": 0.08,
            "character": 0.10,
            "chapter_proximity": 0.07,
        },
        "min_score": 0.25,
        "require_non_character_support": False,
        "duplicate_penalty": 0.10,
        "character_mode": "binary",
    },
    "diversity_020": {
        "weights": {
            "vector": 0.55,
            "keyword": 0.18,
            "title": 0.10,
            "character": 0.12,
            "chapter_proximity": 0.05,
        },
        "min_score": 0.20,
        "require_non_character_support": False,
        "duplicate_penalty": 0.15,
        "character_mode": "binary",
    },
    "wr35_metadata_020": {
        "weights": {
            "vector": 0.50,
            "keyword": 0.15,
            "title": 0.08,
            "character": 0.10,
            "chapter_proximity": 0.04,
            "metadata_evidence": 0.13,
        },
        "min_score": 0.20,
        "require_non_character_support": False,
        "duplicate_penalty": 0.08,
        "character_mode": "binary",
    },
    # Dev-selected calibration from the 2026-08-07 controlled ablation
    # (me=0.16, ch=0.08, min_score=0.20, vector=0.50): all p@5=0.6815,
    # r@5=0.6769, fact=0.3889; Tier B p@5=0.5938, r@5=0.6875, fact=0.4375;
    # gates 5/5 on the production-parity dev harness.
    "wr35_metadata_021": {
        "weights": {
            "vector": 0.50,
            "keyword": 0.15,
            "title": 0.08,
            "character": 0.08,
            "chapter_proximity": 0.04,
            "metadata_evidence": 0.16,
        },
        "min_score": 0.20,
        "require_non_character_support": False,
        "duplicate_penalty": 0.08,
        "character_mode": "binary",
    },
}
