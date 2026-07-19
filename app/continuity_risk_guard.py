"""Deterministic continuity-risk protection for shadow ContextBroker runs.

The guard never edits item text.  It only decides whether an older complete
recent-original item must be restored on top of a frozen budgeted selection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .context_broker import ACTOR_NAMES, ContextItem, _terms


TIME_PATTERN = re.compile(
    r"周[一二三四五六日天]|当天|当晚|次日|第二天|第三天|明天|昨天|今早|今晚|"
    r"凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|半夜|"
    r"(?:一|两|二|三|四|五|六|七|八|九|十|\d+)(?:天|周|个月|年)(?:后|前|了)?"
)
STATE_PATTERN = re.compile(
    r"去世|死了|离世|走了|离开|搬走|住院|出院|辞职|离职|失业|借款|借钱|"
    r"欠款|负债|还款|破产|分手|结婚|怀孕|生病|手术"
)
CHAIN_PATTERN = re.compile(
    r"邀请|约定|答应|承诺|问题|问过|回答|回复|下次|明天|下周|还会|再来|"
    r"还没|尚未|没有回答|没回答|留着|欠着|等待"
)


def _snippet(text: str, start: int, end: int, radius: int = 28) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    value = re.sub(r"\s+", " ", text[left:right]).strip()
    return value[:80]


def _regex_risk(text: str, pattern: re.Pattern[str], risk_type: str) -> dict | None:
    match = pattern.search(text or "")
    if not match:
        return None
    return {
        "risk_type": risk_type,
        "evidence": _snippet(text, match.start(), match.end()),
        "matched_terms": [match.group(0)],
    }


def _shared_terms(left: str, right: str) -> list[str]:
    return sorted(term for term in (_terms(left) & _terms(right)) if len(term) >= 3)


@dataclass(frozen=True)
class ContinuityRiskAssessment:
    protect: bool
    reason: str
    risks: tuple[dict, ...]

    def trace(self) -> dict:
        return {
            "protect": self.protect,
            "reason": self.reason,
            "risks": [dict(item) for item in self.risks],
        }


class ContinuityRiskGuard:
    """Protect older recent originals using explainable, evaluation-free rules."""

    def assess(
        self,
        item: ContextItem,
        *,
        query: str,
        immediate_text: str,
        handover_text: str,
        peer_older_texts: tuple[str, ...] = (),
    ) -> ContinuityRiskAssessment:
        if item.source_type != "recent_original" or item.priority != "P3":
            return ContinuityRiskAssessment(False, "not_older_recent_original", ())

        risks: list[dict] = []
        for pattern, risk_type in (
            (TIME_PATTERN, "relative_time_anchor"),
            (STATE_PATTERN, "durable_character_or_world_state"),
            (CHAIN_PATTERN, "unfinished_interaction_chain"),
        ):
            risk = _regex_risk(item.text, pattern, risk_type)
            if risk:
                risks.append(risk)

        query_terms = _shared_terms(query, item.text)
        immediate_terms = _terms(immediate_text)
        peer_terms = set().union(*(_terms(text) for text in peer_older_texts)) if peer_older_texts else set()
        unique_terms = [term for term in query_terms if term not in immediate_terms and term not in peer_terms]
        if len(unique_terms) >= 2:
            first = item.text.find(unique_terms[0])
            risks.append({
                "risk_type": "unique_current_event_source",
                "evidence": _snippet(item.text, max(0, first), max(0, first) + len(unique_terms[0])),
                "matched_terms": unique_terms[:8],
            })

        handover_terms = _shared_terms(handover_text, item.text)
        if len(handover_terms) >= 2:
            first = item.text.find(handover_terms[0])
            risks.append({
                "risk_type": "handover_explicit_reference",
                "evidence": _snippet(item.text, max(0, first), max(0, first) + len(handover_terms[0])),
                "matched_terms": handover_terms[:8],
            })

        if risks:
            return ContinuityRiskAssessment(True, "continuity_risk_detected", tuple(risks))

        query_actors = {name for name in ACTOR_NAMES if name in (query or "")}
        item_actors = set(item.actors)
        event_overlap = _shared_terms(query, item.text)
        immediate_overlap = _shared_terms(immediate_text, item.text)
        if not (query_actors & item_actors) and not event_overlap and not immediate_overlap and not handover_terms:
            return ContinuityRiskAssessment(False, "clear_continuity_nonmatch", ())

        fallback = {
            "risk_type": "uncertain_continuity_fallback",
            "evidence": _snippet(item.text, 0, min(len(item.text), 1)),
            "matched_terms": (event_overlap or immediate_overlap)[:8],
        }
        return ContinuityRiskAssessment(
            True,
            "unable_to_exclude_continuity_risk_keep_full_item",
            (fallback,),
        )
