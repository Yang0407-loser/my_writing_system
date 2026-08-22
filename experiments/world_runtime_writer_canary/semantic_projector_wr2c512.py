"""WR2-C5.1.2 deterministic projector.

Same boundary as WR2-C5.1.1 (model judges, projector owns canonical fields) plus:

1. Multi-clock fallback: every explicit parseable time later than the state
   clock (outside quotes) that is not already represented gets its own
   evidence-bound clock change.
2. Composite object split: a merged clean_and_stored judgment whose evidence
   also contains an explicit pour action is split into ``empty`` and
   ``clean_and_stored`` changes.

1. Composite model values such as ``empty_and_restored`` are normalized to the
   single canonical state word ``empty`` (the restore part is a no-op).
2. For explicit object actions, if the evidence excerpt omits the actor but
   the full text contains exactly one known character, that character is used
   as the actor; multi-character texts stay conservative.

Legality remains with the frozen WR2-B validator; nothing here commits state.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from experiments.world_runtime_writer_canary.delta_shadow_wr2a import EvidenceSpan
from experiments.world_runtime_writer_canary.delta_shadow_wr2c5 import (
    ProposedChangeV5,
    ProposedTypedDeltaV5,
)
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c import (
    DroppedEvent,
    RawEvidence,
    _find_span,
)


PROJECTOR_VERSION = "world-runtime-projector-wr2c512-v1"

CHARACTER_ALIASES = {
    "林晚": "character:lin-wan",
    "周野": "character:zhou-ye",
    "季晴": "character:ji-qing",
    "阿吴": "character:coworker",
    "老吴": "character:coworker",
    "吴姐": "character:coworker",
}

LOCATION_ALIASES = {
    "操作间": "bakery:wild-bread:workshop",
    "库房": "bakery:wild-bread:workshop",
    "工坊": "bakery:wild-bread:workshop",
    "后厨": "bakery:wild-bread:workshop",
    "临街门": "bakery:wild-bread:storefront",
    "侧门": "bakery:wild-bread:storefront",
    "窗口": "bakery:wild-bread:storefront",
    "柜台": "bakery:wild-bread:storefront",
    "店堂": "bakery:wild-bread:storefront",
    "门店": "bakery:wild-bread:storefront",
    "客厅": "lin-wan-home:living-room",
    "茶几": "lin-wan-home:coffee-table",
    "橱柜": "lin-wan-home:kitchen-cabinet",
    "厨房": "lin-wan-home:kitchen",
}

OBJECT_ALIASES = {
    "碗": "object:green-bean-soup-bowl",
    "绿豆汤": "object:green-bean-soup-bowl",
    "餐包": "object:bread-bag",
    "可颂": "object:bread-bag",
    "牛角包": "object:bread-bag",
    "吐司": "object:bread-bag",
}

ROLE_VALUES = {
    "采购主管": "bakery_procurement_supervisor",
    "排班员": "shift_scheduler",
    "店长": "shop_manager",
    "收银员": "cashier",
}

NAME_IDS = {
    "韩冰": "han-bing",
    "孙岚": "sun-lan",
    "赵敏": "zhao-min",
    "陈青": "chen-qing",
}

_FIXED_SHAPES: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "storefront_public_sale": ("bakery:wild-bread:storefront", ("public_sale_event",)),
    "storefront_public_handoff": ("bakery:wild-bread:storefront", ("public_goods_handoff",)),
    "storefront_operation_state": ("bakery:wild-bread:storefront", ("operation_state",)),
    "knowledge_state": (None, ("article_knowledge",)),
    "resignation_acknowledgement": ("company:lin-wan", ("resignation_acknowledged",)),
    "unsourced_project_fact": (None, ("identity_role", "communication_recipient")),
    "object_state": (None, ("content_state", "temperature_state", "location_state")),
    "repeated_completed_event": ("article:lin-wan", ("publication_event",)),
    "employment_state": ("employment:lin-wan", ("status",)),
    "publication_state": ("article:lin-wan", ("publication_state",)),
    "resignation_delivery": ("resignation:lin-wan", ("lifecycle_state",)),
    "resignation_personal_record": ("resignation:lin-wan", ("personal_record_state",)),
    "clock_state": ("world_clock", ("time",)),
    "location_state": (None, ("location",)),
}

_CHAIN_RANK = {
    "clock_state": 0,
    "storefront_operation_state": 1,
    "storefront_public_sale": 2,
    "storefront_public_handoff": 2,
    "resignation_acknowledgement": 3,
    "employment_state": 4,
}

_COMPOSITE_EMPTY_VALUES = {
    "empty_and_restored",
    "emptied_and_restored",
    "empty_and_put_back",
    "emptied_and_put_back",
    "poured_out_and_restored",
    "poured_out_and_put_back",
}


def _cn_number(token: str | None) -> int | None:
    if token is None:
        return None
    token = token.strip()
    if token.isdigit():
        return int(token)
    digits = {
        "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }
    if token == "十":
        return 10
    if "十" in token:
        left, _, right = token.partition("十")
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(token) == 1:
        return digits.get(token)
    return None


_CLOCK_PATTERNS = (
    re.compile(r"(\d{1,2})[:：](\d{1,2})"),
    re.compile(r"([零一二三四五六七八九十两\d]{1,3})点(?:一刻|([零一二三四五六七八九十两\d]{1,3})(?:分)?)?"),
)


def _parse_clock(text: str) -> str | None:
    for pattern in _CLOCK_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groups()
        if ":" in match.group(0) or "：" in match.group(0):
            hour, minute = int(groups[0]), int(groups[1])
        else:
            hour = _cn_number(groups[0])
            if hour is None:
                continue
            minute = 15 if "一刻" in match.group(0) else (_cn_number(groups[1]) or 0)
        if hour > 23 or minute > 59:
            continue
        return f"{hour:02d}:{minute:02d}"
    return None


def _find_character(text: str) -> str | None:
    for alias, canonical in CHARACTER_ALIASES.items():
        if alias in text:
            return canonical
    return None


def _find_unique_character(full_text: str) -> str | None:
    found = [
        canonical
        for alias, canonical in CHARACTER_ALIASES.items()
        if alias in full_text
    ]
    unique = set(found)
    return next(iter(unique)) if len(unique) == 1 else None


def _find_knowledge_subject(excerpts: list[str]) -> str | None:
    perception_tokens = ("引用", "回复", "问", "读到", "看到", "看完", "说", "念")
    candidates: list[tuple[int, str]] = []
    for excerpt in excerpts:
        for token in perception_tokens:
            token_pos = excerpt.find(token)
            while token_pos != -1:
                for alias, canonical in CHARACTER_ALIASES.items():
                    alias_pos = excerpt.find(alias)
                    while alias_pos != -1:
                        candidates.append((abs(alias_pos - token_pos), canonical))
                        alias_pos = excerpt.find(alias, alias_pos + 1)
                token_pos = excerpt.find(token, token_pos + 1)
    if candidates:
        best = min(candidates, key=lambda item: (item[0], item[1]))
        return best[1]
    for alias, canonical in CHARACTER_ALIASES.items():
        if any(alias in excerpt for excerpt in excerpts):
            return canonical
    return None


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    open_chars = set("“\"『「")
    close_chars = set("”\"』」")
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    for index, char in enumerate(text):
        if char in open_chars:
            stack.append(index)
        elif char in close_chars and stack:
            start = stack.pop()
            spans.append((start, index + 1))
    return spans


def _fallback_clock_events(
    *,
    text: str,
    state,
    judgments: list[dict[str, Any]],
    existing_times: set[str],
) -> list[ProjectedEvent]:
    """Synthesize one clock change per explicit narrative time expression."""

    base = next(
        (
            fact
            for fact in state.facts
            if fact.subject == "world_clock" and fact.predicate == "time"
        ),
        None,
    )
    base_value = (
        base.value
        if base is not None and base.epistemic_status == "confirmed_true" and isinstance(base.value, str)
        else None
    )
    quoted = _quoted_spans(text)
    found: dict[str, ProjectedEvent] = {}
    for pattern in _CLOCK_PATTERNS:
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for start, end in quoted):
                continue
            parsed = _parse_clock(match.group(0))
            if parsed is None:
                continue
            if parsed in existing_times or parsed in found:
                continue
            if base_value is not None and parsed <= base_value:
                continue
            start, end = match.start(), match.end()
            evidence = RawEvidence(excerpt=match.group(0), occurrence=1)
            found[parsed] = ProjectedEvent(
                change_type="clock_state",
                subject="world_clock",
                predicate="time",
                after_value=parsed,
                actor="narrator",
                mechanism="explicit_time_progression",
                evidence=[(evidence, (start, end))],
                span=(start, end),
                judgment_index=len(judgments) + len(found),
                before_value=base_value,
                before_status="confirmed_true" if base_value is not None else "unknown",
            )
    return sorted(found.values(), key=lambda event: event.after_value)


def _find_location(text: str) -> str | None:
    hits = [(alias, canonical) for alias, canonical in LOCATION_ALIASES.items() if alias in text]
    if not hits:
        return None
    hits.sort(key=lambda item: len(item[0]), reverse=True)
    return hits[0][1]


def _find_object(text: str) -> str | None:
    hits = [(alias, canonical) for alias, canonical in OBJECT_ALIASES.items() if alias in text]
    if not hits:
        return None
    hits.sort(key=lambda item: len(item[0]), reverse=True)
    return hits[0][1]


def _evidence_text(excerpts: list[str]) -> str:
    return "".join(excerpts)


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _resolve_sale(evidence: str) -> str:
    if _has_any(evidence, ("收款码", "扫码", "付款", "支付", "转账", "收银机")):
        return "digital_payment_exchange"
    if _has_any(evidence, ("现金", "硬币", "零钱")):
        return "cash_exchange"
    return "cash_exchange"


def _resolve_knowledge_mechanism(evidence: str) -> str:
    if _has_any(evidence, ("链接", "私聊")):
        return "private_link_send_and_body_response"
    if _has_any(evidence, ("文件", "文档", "全文", "定稿", "终稿")) and "群" in evidence:
        return "group_file_send_and_body_response"
    if "群" in evidence:
        return "explicit_group_send_and_body_response"
    return "missing_transmission_path"


def _resolve_employment_mechanism(evidence: str) -> str:
    if _has_any(evidence, ("人事", "邮件", "确认", "受理", "回执", "系统")):
        return "acknowledged_effective_resignation"
    return "self_assumed_effective"


def _resolve_unsourced(evidence: str) -> tuple[str, str, str] | None:
    for role in ROLE_VALUES:
        match = re.search(role + r"([\u4e00-\u9fff]{2})", evidence)
        if match:
            name = match.group(1)
            return f"character:{NAME_IDS.get(name, name)}", "identity_role", ROLE_VALUES[role]
    match = re.search(r"(?:新来的|新任|新招的|新)?(?:主管|店员|排班员|店长|收银员)([\u4e00-\u9fff]{2})", evidence)
    if match:
        name = match.group(1)
        return f"character:{NAME_IDS.get(name, name)}", "communication_recipient", match.group(0)
    return None


def _normalize_after_value(after_value: Any) -> Any:
    if isinstance(after_value, str):
        lowered = after_value.strip().lower()
        if lowered in _COMPOSITE_EMPTY_VALUES:
            return "empty"
    return after_value


def _resolve_object(
    evidence: str,
    after_value: Any,
    full_text: str,
) -> tuple[str, str, Any, str, str] | None:
    subject = _find_object(evidence)
    if subject is None:
        return None
    after_value = _normalize_after_value(after_value)
    clean = _has_any(evidence, ("洗净", "洗好", "洗干净", "洗过"))
    stored = _has_any(evidence, ("收进", "收好", "放进", "摆进", "橱柜", "柜子"))
    empty = _has_any(evidence, ("倒", "泼", "喝光", "喝掉", "倒掉"))
    no_actor = _has_any(evidence, ("没人", "无人", "整夜", "清晨", "早晨", "已经"))
    evidence_actor = _find_character(evidence)
    unique_actor = _find_unique_character(full_text)
    actor_present = evidence_actor is not None or (unique_actor is not None and empty)
    if (after_value == "clean_and_stored") or (clean and stored):
        mechanism = "missing_actor_or_event" if (no_actor or not actor_present) else "explicit_action"
        actor = str(evidence_actor or unique_actor or "narrator/unknown")
        return subject, "content_state", "clean_and_stored", actor, mechanism
    if empty or after_value == "empty":
        mechanism = "actor_pours_out" if actor_present else "missing_actor_or_event"
        actor = str(evidence_actor or unique_actor or "narrator/unknown")
        return subject, "content_state", "empty", actor, mechanism
    if _has_any(evidence, ("冷", "热", "凉", "温")):
        value = "cold" if _has_any(evidence, ("冷", "凉")) else "hot"
        return subject, "temperature_state", value, str(evidence_actor or unique_actor or "narrator/unknown"), "explicit_observation"
    if _has_any(evidence, ("放回", "挪", "摆进", "收进", "留下", "原处", "原位", "搁回")):
        location = _find_location(evidence)
        if location is None and not _has_any(evidence, ("原位", "原处")):
            return None
        return subject, "location_state", location, str(evidence_actor or unique_actor or "narrator/unknown"), "explicit_action"
    return None


def _resolve_judgment(
    change_type: str,
    evidence_text: str,
    after_value: Any,
    excerpts: list[str] | None = None,
    state=None,
    full_text: str = "",
) -> tuple[str, str, Any, str, str] | None:
    """Return (subject, predicate, after_value, actor, mechanism) or None."""
    fixed_subject, predicates = _FIXED_SHAPES[change_type]
    predicate = predicates[0]
    evidence_actor = _find_character(evidence_text)
    unique_actor = _find_unique_character(full_text)
    actor = str(evidence_actor or unique_actor or "narrator")
    if change_type == "storefront_public_sale":
        return fixed_subject, predicate, "occurred", actor, _resolve_sale(evidence_text)
    if change_type == "storefront_public_handoff":
        return fixed_subject, predicate, "occurred", actor, "free_handoff"
    if change_type == "storefront_operation_state":
        after = after_value
        if not isinstance(after, str) or after not in {"open", "closed"}:
            after = "closed" if _has_any(evidence_text, ("关门", "打烊", "闭店")) else "open"
        return fixed_subject, predicate, after, actor, "explicit_open_close"
    if change_type == "knowledge_state":
        subject = _find_knowledge_subject(excerpts or [evidence_text])
        if subject is None:
            return None
        return subject, predicate, "perceived", actor, _resolve_knowledge_mechanism(evidence_text)
    if change_type == "resignation_acknowledgement":
        return fixed_subject, predicate, True, "company:hr-system", "institutional_reply"
    if change_type == "unsourced_project_fact":
        resolved = _resolve_unsourced(evidence_text)
        if resolved is None:
            return None
        return resolved[0], resolved[1], resolved[2], "narrator", "text_assertion"
    if change_type == "object_state":
        resolved = _resolve_object(evidence_text, after_value, full_text)
        if (
            resolved is not None
            and resolved[1] == "location_state"
            and (resolved[2] is None or _has_any(evidence_text, ("原位", "原处", "搁回")))
            and state is not None
        ):
            fact = next(
                (
                    item
                    for item in state.facts
                    if item.subject == resolved[0] and item.predicate == "location"
                ),
                None,
            )
            if fact is not None and fact.epistemic_status == "confirmed_true":
                resolved = (resolved[0], resolved[1], fact.value, resolved[3], resolved[4])
            elif resolved[2] is None:
                return None
        return resolved
    if change_type == "repeated_completed_event":
        return fixed_subject, predicate, "repeated", actor, "explicit_repeat_marker"
    if change_type == "employment_state":
        return fixed_subject, predicate, "ended", actor, _resolve_employment_mechanism(evidence_text)
    if change_type == "publication_state":
        return fixed_subject, predicate, "published", actor, "submit_and_platform_publish"
    if change_type == "resignation_delivery":
        return fixed_subject, predicate, "delivered", actor, "institutional_email_delivery"
    if change_type == "resignation_personal_record":
        return fixed_subject, predicate, "saved", actor, "private_email_copy"
    if change_type == "clock_state":
        parsed = _parse_clock(evidence_text) or (after_value if isinstance(after_value, str) and re.fullmatch(r"\d{2}:\d{2}", after_value) else None)
        if parsed is None:
            return None
        return fixed_subject, predicate, parsed, "narrator", "explicit_time_progression"
    if change_type == "location_state":
        subject = _find_character(evidence_text) or unique_actor
        location = _find_location(evidence_text)
        if subject is None or location is None:
            return None
        return subject, predicate, location, subject, "explicit_entry"
    return None


def _prior(change_type: str, subject: str, predicate: str, state) -> tuple[Any, str]:
    if change_type in {
        "storefront_public_sale",
        "storefront_public_handoff",
        "unsourced_project_fact",
    }:
        return None, "unknown"
    if change_type == "repeated_completed_event":
        return "completed", "confirmed_true"
    if change_type == "object_state" and predicate == "location_state":
        predicate = "location"
    fact = next(
        (
            item
            for item in state.facts
            if item.subject == subject and item.predicate == predicate
        ),
        None,
    )
    if fact is None or fact.epistemic_status == "unknown":
        return None, "unknown"
    return fact.value, "confirmed_true"


class ProjectedEvent:
    __slots__ = (
        "change_type", "subject", "predicate", "after_value", "actor",
        "mechanism", "evidence", "span", "judgment_index",
        "before_value", "before_status",
    )

    def __init__(
        self, *, change_type, subject, predicate, after_value, actor,
        mechanism, evidence, span, judgment_index, before_value, before_status,
    ):
        self.change_type = change_type
        self.subject = subject
        self.predicate = predicate
        self.after_value = after_value
        self.actor = actor
        self.mechanism = mechanism
        self.evidence = evidence
        self.span = span
        self.judgment_index = judgment_index
        self.before_value = before_value
        self.before_status = before_status


def project(
    *,
    text: str,
    state,
    judgments: list[dict[str, Any]],
) -> tuple[list[ProjectedEvent], list[DroppedEvent]]:
    """Project model judgments into canonical events."""
    dropped: list[DroppedEvent] = []
    events: list[ProjectedEvent] = []
    signatures: set[tuple[str, str, str, str, str]] = set()
    for index, judgment in enumerate(judgments):
        change_type = judgment.get("change_type")
        if change_type not in _FIXED_SHAPES:
            dropped.append(DroppedEvent(index=index, reason="unsupported_change_type", change_type=change_type))
            continue
        if not judgment.get("occurred", False):
            dropped.append(DroppedEvent(index=index, reason="judged_not_occurred", change_type=change_type))
            continue
        if judgment.get("mode") != "actual" or judgment.get("epistemic") != "asserted":
            dropped.append(DroppedEvent(
                index=index,
                reason=f"non_actual_or_non_asserted:{judgment.get('mode')}:{judgment.get('epistemic')}",
                change_type=change_type,
            ))
            continue
        raw_evidence = judgment.get("evidence") or []
        if not raw_evidence:
            dropped.append(DroppedEvent(index=index, reason="evidence_missing", change_type=change_type))
            continue
        resolved: list[tuple[RawEvidence, tuple[int, int]]] = []
        for item in raw_evidence:
            evidence = RawEvidence.model_validate(item)
            span = _find_span(text, evidence)
            if span is None:
                resolved = []
                break
            resolved.append((evidence, span))
        if not resolved:
            dropped.append(DroppedEvent(index=index, reason="evidence_not_found", change_type=change_type))
            continue
        excerpts = [item.excerpt for item, _ in resolved]
        canonical = _resolve_judgment(
            change_type,
            _evidence_text(excerpts),
            judgment.get("after_value"),
            excerpts=excerpts,
            state=state,
            full_text=text,
        )
        if canonical is None:
            dropped.append(DroppedEvent(index=index, reason="projection_unresolved", change_type=change_type))
            continue
        evidence_text_all = _evidence_text(excerpts)
        canonicals: list[tuple[str, str, Any, str, str]] = [canonical]
        if (
            change_type == "object_state"
            and canonical[1] == "content_state"
            and canonical[2] == "clean_and_stored"
            and _has_any(evidence_text_all, ("倒", "泼"))
            and _has_any(evidence_text_all, ("洗净", "洗好", "洗干净"))
            and _has_any(evidence_text_all, ("收进", "放进", "摆进", "柜子"))
        ):
            actor_present = (
                _find_character(evidence_text_all) is not None
                or _find_unique_character(text) is not None
            )
            actor_id = str(
                _find_character(evidence_text_all)
                or _find_unique_character(text)
                or "narrator/unknown"
            )
            canonicals = [
                (
                    canonical[0], "content_state", "empty", actor_id,
                    "actor_pours_out" if actor_present else "missing_actor_or_event",
                ),
                (
                    canonical[0], "content_state", "clean_and_stored", actor_id,
                    "explicit_action" if actor_present else "missing_actor_or_event",
                ),
            ]
        for canonical_item in canonicals:
            subject, predicate, after_value, actor, mechanism = canonical_item
            before_value, before_status = _prior(change_type, subject, predicate, state)
            if before_status == "confirmed_true" and json.dumps(after_value, ensure_ascii=False, sort_keys=True) == json.dumps(before_value, ensure_ascii=False, sort_keys=True):
                dropped.append(DroppedEvent(index=index, reason="no_state_change", change_type=change_type))
                continue
            signature = (
                change_type,
                subject,
                predicate,
                json.dumps(after_value, ensure_ascii=False, sort_keys=True),
                mechanism,
            )
            if signature in signatures:
                dropped.append(DroppedEvent(index=index, reason="duplicate_semantic_change", change_type=change_type))
                continue
            signatures.add(signature)
            events.append(ProjectedEvent(
                change_type=change_type,
                subject=subject,
                predicate=predicate,
                after_value=after_value,
                actor=actor,
                mechanism=mechanism,
                evidence=resolved,
                span=(min(item[1][0] for item in resolved), max(item[1][1] for item in resolved)),
                judgment_index=index,
                before_value=before_value,
                before_status=before_status,
            ))
    if any(event.change_type == "storefront_public_sale" for event in events):
        free_tokens = ("免费", "送", "请客", "没接钱")
        kept: list[ProjectedEvent] = []
        for event in events:
            if event.change_type == "storefront_public_handoff":
                evidence_text = _evidence_text([item.excerpt for item, _ in event.evidence])
                if not _has_any(evidence_text, free_tokens):
                    dropped.append(DroppedEvent(
                        index=event.judgment_index,
                        reason="handoff_subsumed_by_sale",
                        change_type=event.change_type,
                    ))
                    continue
            kept.append(event)
        events = kept
    events.sort(key=lambda event: (_CHAIN_RANK.get(event.change_type, 9), event.judgment_index))
    existing_times = {event.after_value for event in events if event.change_type == "clock_state"}
    fallback = _fallback_clock_events(
        text=text,
        state=state,
        judgments=judgments,
        existing_times=existing_times,
    )
    if fallback:
        events.extend(fallback)
        events.sort(key=lambda event: (_CHAIN_RANK.get(event.change_type, 9), event.judgment_index))
    return events, dropped


def build_delta(
    *,
    text: str,
    sample_id: str,
    scene_id: str,
    state_variant: str,
    base_revision: int,
    events: list[ProjectedEvent],
) -> tuple[ProposedTypedDeltaV5, list[EvidenceSpan]]:
    evidence_spans: list[EvidenceSpan] = []
    changes: list[ProposedChangeV5] = []
    for sequence, event in enumerate(events, 1):
        event_evidence_ids: list[str] = []
        for evidence_index, (evidence, span) in enumerate(event.evidence, 1):
            evidence_id = f"ev:wr2c512:{sample_id.lower()}:{sequence}:{evidence_index}"
            evidence_spans.append(EvidenceSpan(
                evidence_id=evidence_id,
                claim=f"semantic judgment for {event.change_type}",
                start=span[0],
                end=span[1],
                excerpt=evidence.excerpt,
            ))
            event_evidence_ids.append(evidence_id)
        changes.append(ProposedChangeV5(
            change_id=f"change:wr2c512:{sample_id.lower()}:{sequence}",
            sequence=sequence,
            change_type=event.change_type,
            subject=event.subject,
            predicate=event.predicate,
            before_value=event.before_value,
            before_epistemic_status=event.before_status,
            after_value=event.after_value,
            actor=event.actor,
            mechanism=event.mechanism,
            event_id=None,
            evidence_ids=tuple(event_evidence_ids),
        ))
    return ProposedTypedDeltaV5(
        delta_id=f"delta:wr2c512:{sample_id.lower()}",
        sample_id=sample_id,
        scene_id=scene_id,
        project_id="project:saturday-bakery",
        state_variant=state_variant,
        base_revision=base_revision,
        output_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        evidence=tuple(evidence_spans),
        changes=tuple(changes),
    ), evidence_spans
