"""Compact transport for the evidence-backed handover V2 contract.

The model emits only source indexes, spans, compact enums, and short semantic
text. Authoritative provenance and the V2 contract are reconstructed locally.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..utils.llm_client import estimate_tokens
from .handover_contract_v2 import (
    MAX_EVIDENCE_EXCERPT,
    HandoverClaim,
    HandoverContractValidatorV2,
    HandoverEvidence,
    HandoverNextBoundary,
    HandoverOpenEvent,
    HandoverRejection,
    HandoverSource,
    HandoverValidationResult,
    RejectionReason,
    canonical_json,
    sha256_json,
)


HANDOVER_COMPACT_V21_VERSION = "2.1"
HANDOVER_COMPACT_V21_MAX_OUTPUT_TOKENS = 600
# ── V2.2 短引锚定传输层 ──
# 2026-07-26 第二次真实 Demo：33/37 item 死于 invalid_span——模型无法在千字级
# 文本中数出精确字符偏移（且无一条到达 evidence_text_mismatch，偏移系统性越
# 界）。V2.2 用"≤20 字原文逐字短引"替代 [start,end]，parser 以 find() 定位并
# 重建精确 span。逐字性要求不降：短引找不到即拒。
# 输出上限 600→1000：短引使最坏合法 payload 增大（12 item × 最长 20 字引文，
# 684 字符，本机真实 estimate_tokens 实测 813）。初版上限 800 经实测余量为
# -13，按既有门槛公式（上限 − 最坏合法 ≥ 100）上调至 1000，实测余量 ≈187。
# 上限只约束失控输出；两次真实 Demo 的实际输出为 178~339 tokens。
HANDOVER_COMPACT_V22_VERSION = "2.2"
HANDOVER_COMPACT_V22_MAX_OUTPUT_TOKENS = 1000
MIN_QUOTE_CHARS = 4
MAX_QUOTE_CHARS = 20
MAX_COMPACT_TEXT = 16
MAX_END_STATES = 4
MAX_OPEN_EVENTS = 3
MAX_NEW_FACTS = 3
MAX_ARC_PROGRESS = 2

_SOURCE_ORDER = {
    "generated_subsection": 0,
    "current_outline": 1,
    "next_outline": 2,
    "arc_milestone": 3,
}
_CATEGORY_CODES = {
    "ts": "time_state",
    "ls": "location_state",
    "cs": "character_state",
    "rs": "relationship_state",
    "kf": "known_fact",
    "os": "object_or_resource_state",
    "fs": "foreshadow_state",
}
_STATE_CATEGORIES = {"ts", "ls", "cs", "rs", "os", "fs"}
_FACT_CATEGORIES = {"ts", "ls", "kf", "os", "fs"}
_TEMPORAL_CODES = {
    "c": "current",
    "p": "past",
    "pl": "planned",
    "co": "conditional",
    "u": "unknown",
}
_CERTAINTY_CODES = {"c": "confirmed", "u": "explicit_unknown"}
_COMPLETION_CODES = {
    "o": "open",
    "p": "partially_completed",
    "c": "completed",
    "u": "unknown",
}
_ARC_COMPLETION_CODES = {
    "p": "partially_completed",
    "c": "completed",
    "u": "unknown",
}


class CompactSourceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=0)
    source: HandoverSource
    milestone_event_id: str = ""
    milestone_character_id: str = ""

    def public_manifest(self) -> dict[str, Any]:
        return {
            "index": self.index,
            **self.source.public_manifest(),
            "milestone_event_id": self.milestone_event_id,
            "milestone_character_id": self.milestone_character_id,
        }


class CompactSourceRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[CompactSourceEntry, ...]

    @property
    def registry_hash(self) -> str:
        return sha256_json([item.public_manifest() for item in self.entries])

    def get(self, index: int) -> CompactSourceEntry | None:
        if 0 <= index < len(self.entries):
            item = self.entries[index]
            return item if item.index == index else None
        return None


class CompactHandoverPayloadV21(BaseModel):
    """Strict top-level shape; individual items are validated independently."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Literal["2.1"]
    s: list[Any] = Field(default_factory=list, max_length=MAX_END_STATES)
    o: list[Any] = Field(default_factory=list, max_length=MAX_OPEN_EVENTS)
    f: list[Any] = Field(default_factory=list, max_length=MAX_NEW_FACTS)
    a: list[Any] = Field(default_factory=list, max_length=MAX_ARC_PROGRESS)


class CompactHandoverPayloadV22(BaseModel):
    """V2.2 top-level shape: identical lists, items are quote-anchored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Literal["2.2"]
    s: list[Any] = Field(default_factory=list, max_length=MAX_END_STATES)
    o: list[Any] = Field(default_factory=list, max_length=MAX_OPEN_EVENTS)
    f: list[Any] = Field(default_factory=list, max_length=MAX_NEW_FACTS)
    a: list[Any] = Field(default_factory=list, max_length=MAX_ARC_PROGRESS)


class CompactHandoverPayloadV23(BaseModel):
    """V2.3「引文即主张」：短语层退役，item 只含定位短引与分类标签。

    设计动因（Demo #4–#6 累计归因）：短语逐字包含检查在中文叙事上双向失效
    ——代词/省略句拒真（假阴性），多名句无法验证归因（假阳性）；16 字预算
    与逐字覆盖内在冲突。短语唯一不可替代的贡献（实体绑定）恰是逐字检查
    验证不了的，降级为另行立项的 proposed 层。claim = 逐字原文短引 + 类别/
    时间/确定性标签；契约只承诺逐字溯源，不承诺分类正确。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Literal["2.3"]
    s: list[Any] = Field(default_factory=list, max_length=MAX_END_STATES)
    o: list[Any] = Field(default_factory=list, max_length=MAX_OPEN_EVENTS)
    f: list[Any] = Field(default_factory=list, max_length=MAX_NEW_FACTS)
    a: list[Any] = Field(default_factory=list, max_length=MAX_ARC_PROGRESS)


def build_compact_source_registry(
    sources: Mapping[str, HandoverSource],
    *,
    arc_milestones: Sequence[Any] = (),
) -> CompactSourceRegistry:
    milestone_by_source = {
        str(getattr(item, "source_id", "") or ""): item
        for item in arc_milestones
        if str(getattr(item, "source_id", "") or "")
    }
    ordered = sorted(
        sources.values(),
        key=lambda item: (_SOURCE_ORDER[item.source_type], item.source_id),
    )
    entries = []
    for index, source in enumerate(ordered):
        milestone = milestone_by_source.get(source.source_id)
        entries.append(
            CompactSourceEntry(
                index=index,
                source=source,
                milestone_event_id=(
                    str(getattr(milestone, "event_id", "") or "")
                    if milestone is not None
                    else ""
                ),
                milestone_character_id=(
                    str(getattr(milestone, "character_id", "") or "")
                    if milestone is not None
                    else ""
                ),
            )
        )
    return CompactSourceRegistry(entries=tuple(entries))


def render_v21_prompt_context(registry: CompactSourceRegistry) -> dict[str, str]:
    rows = []
    for entry in registry.entries:
        source = entry.source
        # 保尾窗口（2026-07-27）：提取目标是节尾状态，超长正文展示末尾而非开头
        # 3000字。切片保持连续，故任何展示中的内容都能被 find() 在全文中定位。
        text = source.text[-3000:] if source.source_type == "generated_subsection" else source.text
        rows.append(
            [
                entry.index,
                source.source_type,
                entry.milestone_event_id,
                entry.milestone_character_id,
                text,
            ]
        )
    return {"source_registry": canonical_json(rows)}


# 恢复层抛出的 ValueError 消息若是 RejectionReason 的合法成员，将按原样进入
# rejection_counts。2026-07-26 唯一真实 Demo 的 32/32 拒绝全部记为笼统的
# `invalid_contract_shape`，导致失败层级无法离线归因——细分理由是该收口
# 授权的两项后续工程之一。
_KNOWN_REJECTION_REASONS = frozenset(get_args(RejectionReason))
_SHAPE_REJECTION_REASONS = frozenset(
    {"invalid_claim_shape", "invalid_open_event_shape", "invalid_arc_shape"}
)


def shape_skeleton(item: Any) -> str:
    """Content-free structural signature of a rejected item.

    2026-07-26 V2.2 Demo：S1.2/S1.4 共 19 个 item 全部死于 arity 错误，但
    sidecar 不保存模型原始输出，"错成了什么形状"完全不可知。骨架只含
    容器类型、长度与元素类型名（int/str/float/bool/dict/...），不含任何
    内容字符——隐私约束不破，形状归因可得。
    """
    if isinstance(item, list):
        inner = ",".join(type(element).__name__ for element in item[:12])
        return f"list[{len(item)}]:{inner}"
    if isinstance(item, dict):
        return f"dict[{len(item)}]"
    return type(item).__name__


def _rejection(item_type: str, item_id: str, detail: str = "") -> HandoverRejection:
    reason = detail if detail in _KNOWN_REJECTION_REASONS else "invalid_contract_shape"
    return HandoverRejection(
        item_type=item_type,
        item_id=item_id,
        rejection_reason=reason,
    )


def _compact_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("invalid_compact_text")
    return text


def _semantic_parts(value: Any) -> tuple[str, str, str]:
    text = _compact_text(value)
    parts = text.split("|")
    if len(parts) != 3:
        raise ValueError("invalid_semantic_parts")
    subject, predicate, object_value = (item.strip() for item in parts)
    if not subject or not predicate:
        raise ValueError("missing_semantic_component")
    # Prompt 的承诺是"三段短语合计不超过16字"——按三段之和计，不含 | 分隔符。
    # 2026-07-26 对拍证实：按 raw 长度计会把 Prompt 合法短语（三段合计16、
    # raw 18）整条拒掉，是 32/32 `invalid_contract_shape` 的已证实来源之一。
    if len(subject) + len(predicate) + len(object_value) > MAX_COMPACT_TEXT:
        raise ValueError("invalid_compact_text")
    return subject, predicate, object_value


def _evidence(
    registry: CompactSourceRegistry,
    source_index: Any,
    start: Any,
    end: Any,
) -> tuple[HandoverEvidence, CompactSourceEntry]:
    if isinstance(source_index, bool) or not isinstance(source_index, int):
        raise ValueError("invalid_source_index")
    entry = registry.get(source_index)
    if entry is None:
        raise ValueError("invalid_source_index")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (start, end)):
        raise ValueError("invalid_span")
    source = entry.source
    if not 0 <= start < end <= len(source.text):
        raise ValueError("invalid_span")
    excerpt = source.text[start:end]
    if not excerpt:
        raise ValueError("empty_span")
    if len(excerpt) > 140:
        raise ValueError("span_too_long")
    return (
        HandoverEvidence(
            source_type=source.source_type,
            source_id=source.source_id,
            source_hash=source.source_hash,
            start=start,
            end=end,
            excerpt=excerpt,
        ),
        entry,
    )


_SENTENCE_ENDERS = "。！？；…\n"
_CLOSING_QUOTE_CHARS = "”’」』）"


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Deterministic sentence bounds around a located match region.

    Backward from the match start to the previous sentence ender (skipping the
    previous sentence's closing-quote chars and whitespace), forward from the
    match end through the next ender plus its trailing closing quotes. A
    newline ender is treated as a boundary but not included in the excerpt.
    """
    sent_start = start
    while sent_start > 0 and text[sent_start - 1] not in _SENTENCE_ENDERS:
        sent_start -= 1
    while sent_start < start and text[sent_start] in _CLOSING_QUOTE_CHARS + " \t\r\n":
        sent_start += 1
    sent_end = end
    while sent_end < len(text) and text[sent_end] not in _SENTENCE_ENDERS:
        sent_end += 1
    if sent_end < len(text) and text[sent_end] != "\n":
        sent_end += 1
        while sent_end < len(text) and text[sent_end] in _CLOSING_QUOTE_CHARS:
            sent_end += 1
    return sent_start, sent_end


def _evidence_from_quote(
    registry: CompactSourceRegistry,
    source_index: Any,
    quote: Any,
) -> tuple[HandoverEvidence, CompactSourceEntry]:
    """Locate a verbatim quote in the authoritative source and rebuild the span.

    Replaces model-emitted [start, end]: the 2026-07-26 demo #2 proved LLMs
    cannot count character offsets (33/37 invalid_span, none in-bounds).
    Quoting is what they CAN do; exactness is preserved because a quote that
    does not appear verbatim in the source is rejected outright. First
    occurrence wins deterministically.
    """
    if isinstance(source_index, bool) or not isinstance(source_index, int):
        raise ValueError("invalid_source_index")
    entry = registry.get(source_index)
    if entry is None:
        raise ValueError("invalid_source_index")
    if not isinstance(quote, str):
        raise ValueError("invalid_quote")
    normalized = quote.strip()
    if not MIN_QUOTE_CHARS <= len(normalized) <= MAX_QUOTE_CHARS:
        raise ValueError("invalid_quote")
    source = entry.source
    start = source.text.find(normalized)
    if start < 0:
        raise ValueError("quote_not_found")
    end = start + len(normalized)
    # 整句证据窗口（2026-07-27，Demo #5 成因一）：短引仍是定位器，但证据
    # span/excerpt 扩展为短引所在完整句——仍为原文逐字、span 精确。中文正文
    # 大量代词/承前省略，角色名往往在句中而不在 20 字短引内；语义校验对句
    # 而非对短引，消灭"引文逐字命中却因主语名不在短引而被拒"的结构性失败。
    # 句子超过 MAX_EVIDENCE_EXCERPT 时回退为短引本身（保持 validator 一致性）。
    sent_start, sent_end = _sentence_bounds(source.text, start, end)
    if sent_end - sent_start > MAX_EVIDENCE_EXCERPT:
        sent_start, sent_end = start, end
    return (
        HandoverEvidence(
            source_type=source.source_type,
            source_id=source.source_id,
            source_hash=source.source_hash,
            start=sent_start,
            end=sent_end,
            excerpt=source.text[sent_start:sent_end],
            # anchor=模型断言的短引；validator 的时态标记检查以此为准，
            # 消除整句证据下无关状态否定（"没有…"）的系统性误伤（Demo #6）。
            anchor=normalized,
        ),
        entry,
    )


def _restore_claim(
    item: Any,
    *,
    registry: CompactSourceRegistry,
    allowed_categories: set[str],
) -> HandoverClaim:
    if not isinstance(item, list) or len(item) != 7:
        raise ValueError("invalid_claim_shape")
    source_index, start, end, category, temporal, certainty, text = item
    if category not in allowed_categories:
        raise ValueError("invalid_category")
    if temporal not in _TEMPORAL_CODES or certainty not in _CERTAINTY_CODES:
        raise ValueError("invalid_claim_enum")
    evidence, _ = _evidence(registry, source_index, start, end)
    subject, predicate, object_value = _semantic_parts(text)
    return HandoverClaim(
        claim_id=f"v21-claim-{sha256_json(item)[:16]}",
        category=_CATEGORY_CODES[category],
        subject=subject,
        predicate=predicate,
        object=object_value,
        temporal_status=_TEMPORAL_CODES[temporal],
        certainty=_CERTAINTY_CODES[certainty],
        evidence=(evidence,),
        provenance="handover_extractor_v2.1",
    )


def _restore_open_event(
    item: Any,
    *,
    registry: CompactSourceRegistry,
) -> HandoverOpenEvent:
    if not isinstance(item, list) or len(item) != 5:
        raise ValueError("invalid_open_event_shape")
    source_index, start, end, status, text = item
    if status not in _COMPLETION_CODES:
        raise ValueError("invalid_completion_status")
    evidence, entry = _evidence(registry, source_index, start, end)
    actors_text, action, object_value = _semantic_parts(text)
    actors = tuple(item.strip() for item in actors_text.split(",") if item.strip())
    if not actors:
        raise ValueError("missing_open_event_actor")
    if any(item not in evidence.excerpt for item in (*actors, action, object_value) if item):
        raise ValueError("unsupported_open_event_component")
    return HandoverOpenEvent(
        event_id=f"v21-open-{sha256_json(item)[:16]}",
        actors=actors,
        action=action,
        object=object_value,
        completion_status=_COMPLETION_CODES[status],
        evidence=(evidence,),
        source_hash=entry.source.source_hash,
    )


def _restore_arc_progress(
    item: Any,
    *,
    registry: CompactSourceRegistry,
) -> dict[str, Any]:
    if not isinstance(item, list) or len(item) != 5:
        raise ValueError("invalid_arc_shape")
    milestone_index, evidence_index, start, end, status = item
    milestone = (
        registry.get(milestone_index)
        if isinstance(milestone_index, int) and not isinstance(milestone_index, bool)
        else None
    )
    if (
        milestone is None
        or milestone.source.source_type != "arc_milestone"
        or not milestone.milestone_event_id
        or not milestone.milestone_character_id
    ):
        raise ValueError("invalid_milestone_source")
    if status not in _ARC_COMPLETION_CODES:
        raise ValueError("invalid_arc_status")
    evidence, _ = _evidence(registry, evidence_index, start, end)
    return {
        "character_id": milestone.milestone_character_id,
        "event_id": milestone.milestone_event_id,
        "completion_status": _ARC_COMPLETION_CODES[status],
        "milestone_source_id": milestone.source.source_id,
        "milestone_source_hash": milestone.source.source_hash,
        "evidence": [evidence.model_dump(mode="json")],
    }


def _restore_and_validate(
    compact: Any,
    *,
    registry: CompactSourceRegistry,
    next_boundary: HandoverNextBoundary,
    stale_completed_claim_hashes: Sequence[str],
    claim_restorer: Any,
    open_event_restorer: Any,
    arc_restorer: Any,
) -> HandoverValidationResult:
    """Shared restore core for the compact transports (v2.1 spans, v2.2 quotes)."""
    raw: dict[str, list[Any]] = {"claims": [], "open_events": [], "arc_progress": []}
    local_rejections: list[HandoverRejection] = []
    shape_skeletons: Counter[str] = Counter()

    def _record_local(item_type: str, item_id: str, item: Any, exc: Exception) -> None:
        detail = str(exc)
        if detail in _SHAPE_REJECTION_REASONS:
            shape_skeletons[shape_skeleton(item)] += 1
        local_rejections.append(_rejection(item_type, item_id, detail=detail))

    for kind, items, allowed in (
        ("state", compact.s, _STATE_CATEGORIES),
        ("fact", compact.f, _FACT_CATEGORIES),
    ):
        for index, item in enumerate(items):
            try:
                raw["claims"].append(
                    claim_restorer(item, registry=registry, allowed_categories=allowed).model_dump(mode="json")
                )
            except (TypeError, ValueError, ValidationError) as exc:
                _record_local("claim", f"{kind}:{index}", item, exc)
    for index, item in enumerate(compact.o):
        try:
            raw["open_events"].append(
                open_event_restorer(item, registry=registry).model_dump(mode="json")
            )
        except (TypeError, ValueError, ValidationError) as exc:
            _record_local("open_event", f"open:{index}", item, exc)
    for index, item in enumerate(compact.a):
        try:
            raw["arc_progress"].append(
                arc_restorer(item, registry=registry)
            )
        except (TypeError, ValueError, ValidationError) as exc:
            _record_local("arc_progress", f"arc:{index}", item, exc)

    validation = HandoverContractValidatorV2().validate(
        raw,
        sources={entry.source.source_id: entry.source for entry in registry.entries},
        next_boundary=next_boundary,
        stale_completed_claim_hashes=stale_completed_claim_hashes,
    )
    update: dict[str, Any] = {}
    if shape_skeletons:
        update["rejection_shape_skeletons"] = dict(shape_skeletons)
    if local_rejections:
        rejections = (*local_rejections, *validation.rejections)
        counts = Counter(item.rejection_reason for item in rejections)
        update.update(
            {
                "rejections": rejections,
                "rejected_claim_count": len(rejections),
                "rejection_counts": dict(counts),
            }
        )
    return validation.model_copy(update=update) if update else validation


def restore_and_validate_v21(
    payload: Mapping[str, Any],
    *,
    registry: CompactSourceRegistry,
    next_boundary: HandoverNextBoundary,
    stale_completed_claim_hashes: Sequence[str] = (),
) -> HandoverValidationResult:
    try:
        compact = CompactHandoverPayloadV21.model_validate(payload)
    except ValidationError as error:
        raise ValueError("InvalidCompactHandoverPayload") from error
    return _restore_and_validate(
        compact,
        registry=registry,
        next_boundary=next_boundary,
        stale_completed_claim_hashes=stale_completed_claim_hashes,
        claim_restorer=_restore_claim,
        open_event_restorer=_restore_open_event,
        arc_restorer=_restore_arc_progress,
    )


# ── V2.2 quote-anchored restorers ──


def _restore_claim_v22(
    item: Any,
    *,
    registry: CompactSourceRegistry,
    allowed_categories: set[str],
) -> HandoverClaim:
    if not isinstance(item, list) or len(item) != 6:
        raise ValueError("invalid_claim_shape")
    source_index, quote, category, temporal, certainty, text = item
    if category not in allowed_categories:
        raise ValueError("invalid_category")
    if temporal not in _TEMPORAL_CODES or certainty not in _CERTAINTY_CODES:
        raise ValueError("invalid_claim_enum")
    evidence, _ = _evidence_from_quote(registry, source_index, quote)
    subject, predicate, object_value = _semantic_parts(text)
    return HandoverClaim(
        claim_id=f"v22-claim-{sha256_json(item)[:16]}",
        category=_CATEGORY_CODES[category],
        subject=subject,
        predicate=predicate,
        object=object_value,
        temporal_status=_TEMPORAL_CODES[temporal],
        certainty=_CERTAINTY_CODES[certainty],
        evidence=(evidence,),
        provenance="handover_extractor_v2.2",
    )


def _restore_open_event_v22(
    item: Any,
    *,
    registry: CompactSourceRegistry,
) -> HandoverOpenEvent:
    if not isinstance(item, list) or len(item) != 4:
        raise ValueError("invalid_open_event_shape")
    source_index, quote, status, text = item
    if status not in _COMPLETION_CODES:
        raise ValueError("invalid_completion_status")
    evidence, entry = _evidence_from_quote(registry, source_index, quote)
    actors_text, action, object_value = _semantic_parts(text)
    actors = tuple(part.strip() for part in actors_text.split(",") if part.strip())
    if not actors:
        raise ValueError("missing_open_event_actor")
    if any(part not in evidence.excerpt for part in (*actors, action, object_value) if part):
        raise ValueError("unsupported_open_event_component")
    return HandoverOpenEvent(
        event_id=f"v22-open-{sha256_json(item)[:16]}",
        actors=actors,
        action=action,
        object=object_value,
        completion_status=_COMPLETION_CODES[status],
        evidence=(evidence,),
        source_hash=entry.source.source_hash,
    )


def _restore_arc_progress_v22(
    item: Any,
    *,
    registry: CompactSourceRegistry,
) -> dict[str, Any]:
    if not isinstance(item, list) or len(item) != 4:
        raise ValueError("invalid_arc_shape")
    milestone_index, evidence_index, quote, status = item
    milestone = (
        registry.get(milestone_index)
        if isinstance(milestone_index, int) and not isinstance(milestone_index, bool)
        else None
    )
    if (
        milestone is None
        or milestone.source.source_type != "arc_milestone"
        or not milestone.milestone_event_id
        or not milestone.milestone_character_id
    ):
        raise ValueError("invalid_milestone_source")
    if status not in _ARC_COMPLETION_CODES:
        raise ValueError("invalid_arc_status")
    evidence, _ = _evidence_from_quote(registry, evidence_index, quote)
    return {
        "character_id": milestone.milestone_character_id,
        "event_id": milestone.milestone_event_id,
        "completion_status": _ARC_COMPLETION_CODES[status],
        "milestone_source_id": milestone.source.source_id,
        "milestone_source_hash": milestone.source.source_hash,
        "evidence": [evidence.model_dump(mode="json")],
    }


def restore_and_validate_v22(
    payload: Mapping[str, Any],
    *,
    registry: CompactSourceRegistry,
    next_boundary: HandoverNextBoundary,
    stale_completed_claim_hashes: Sequence[str] = (),
) -> HandoverValidationResult:
    try:
        compact = CompactHandoverPayloadV22.model_validate(payload)
    except ValidationError as error:
        raise ValueError("InvalidCompactHandoverPayload") from error
    return _restore_and_validate(
        compact,
        registry=registry,
        next_boundary=next_boundary,
        stale_completed_claim_hashes=stale_completed_claim_hashes,
        claim_restorer=_restore_claim_v22,
        open_event_restorer=_restore_open_event_v22,
        arc_restorer=_restore_arc_progress_v22,
    )


def compact_payload_metrics(
    payload: Mapping[str, Any], *, version: str = "2.1"
) -> dict[str, int]:
    encoded = canonical_json(payload)
    empty_wrapper = canonical_json({"v": version, "s": [], "o": [], "f": [], "a": []})
    return {
        "characters": len(encoded),
        "estimated_tokens": estimate_tokens(encoded),
        "wrapper_tokens": estimate_tokens(empty_wrapper),
    }


def typical_compact_payload() -> dict[str, Any]:
    return {
        "v": "2.1",
        "s": [[0, 10, 24, "cs", "c", "c", "林晚|继续记录|见闻"]],
        "o": [[0, 30, 45, "o", "林晚|等待回应|邀请"]],
        "f": [[0, 50, 62, "kf", "c", "c", "相册|留在|书店"]],
        "a": [[3, 0, 70, 82, "p"]],
    }


def worst_legal_compact_payload() -> dict[str, Any]:
    # 三段之和恰为 MAX_COMPACT_TEXT（1+14+1=16），raw 含分隔符 18——
    # 与"三段合计不超过16字"的 Prompt 语义对齐后的新最坏合法形状。
    text = "人|" + "状" * (MAX_COMPACT_TEXT - 2) + "|物"
    return {
        "v": "2.1",
        "s": [[0, i, i + 1, "cs", "c", "c", text] for i in range(MAX_END_STATES)],
        "o": [[0, 10 + i, 11 + i, "o", text] for i in range(MAX_OPEN_EVENTS)],
        "f": [[0, 20 + i, 21 + i, "kf", "c", "c", text] for i in range(MAX_NEW_FACTS)],
        "a": [[3, 0, 30 + i, 31 + i, "p"] for i in range(MAX_ARC_PROGRESS)],
    }


def prompt_example_premise_v22() -> str:
    """The hypothetical source text the V2.2 prompt's worked example cites."""
    return "林晚放下相机，仍在等待周野的回应。"


def prompt_example_payload_v22() -> dict[str, Any]:
    """The exact worked example embedded in HANDOVER_EXTRACTION_PROMPT_V22.

    Demo #3（2026-07-26，任务 50e45671）：同一 Prompt 下模型两个小节格式全对、
    两个小节整节 arity 错乱（19/39），另有 8 条引文质量失败。worked example
    是治格式方差的标准手段；本函数与 Prompt 内嵌文本的逐字一致性、以及
    示例本身能通过真实 parser 端到端恢复，均由工程 gate 强制。
    """
    return {
        "v": "2.2",
        "s": [[0, "林晚放下相机", "cs", "c", "c", "林晚|放下|相机"]],
        "o": [[0, "林晚放下相机，仍在等待周野的回应", "o", "林晚|等待|回应"]],
        "f": [],
        "a": [],
    }


def typical_compact_payload_v22() -> dict[str, Any]:
    return {
        "v": "2.2",
        "s": [[0, "林晚决定继续记录见闻", "cs", "c", "c", "林晚|继续记录|见闻"]],
        "o": [[0, "林晚等待周野回应邀请", "o", "林晚|等待回应|邀请"]],
        "f": [[0, "相册被留在书店里", "kf", "c", "c", "相册|留在|书店"]],
        "a": [[3, 0, "她翻开了那本相册", "p"]],
    }


def worst_legal_compact_payload_v22() -> dict[str, Any]:
    # 每 item 携带最长 20 字短引 + 三段合计 16 字短语——V2.2 的最坏合法形状。
    quote = "引" * MAX_QUOTE_CHARS
    text = "人|" + "状" * (MAX_COMPACT_TEXT - 2) + "|物"
    return {
        "v": "2.2",
        "s": [[0, quote, "cs", "c", "c", text] for _ in range(MAX_END_STATES)],
        "o": [[0, quote, "o", text] for _ in range(MAX_OPEN_EVENTS)],
        "f": [[0, quote, "kf", "c", "c", text] for _ in range(MAX_NEW_FACTS)],
        "a": [[3, 0, quote, "p"] for _ in range(MAX_ARC_PROGRESS)],
    }


# ── V2.3 引文即主张传输层 ──
# Demo #4–#6：形状层与引文层已被模型掌握（arity 三连清零、本地恢复率 73%），
# 拒绝集中于短语对齐规则；该规则双向失效且与 16 字预算内在冲突，整体退役。

HANDOVER_COMPACT_V23_VERSION = "2.3"
HANDOVER_COMPACT_V23_MAX_OUTPUT_TOKENS = 1000


def _restore_claim_v23(
    item: Any,
    *,
    registry: CompactSourceRegistry,
    allowed_categories: set[str],
) -> HandoverClaim:
    if not isinstance(item, list) or len(item) != 5:
        raise ValueError("invalid_claim_shape")
    source_index, quote, category, temporal, certainty = item
    if category not in allowed_categories:
        raise ValueError("invalid_category")
    if temporal not in _TEMPORAL_CODES or certainty not in _CERTAINTY_CODES:
        raise ValueError("invalid_claim_enum")
    evidence, _ = _evidence_from_quote(registry, source_index, quote)
    return HandoverClaim(
        claim_id=f"v23-claim-{sha256_json(item)[:16]}",
        category=_CATEGORY_CODES[category],
        subject="",
        predicate="",
        object="",
        temporal_status=_TEMPORAL_CODES[temporal],
        certainty=_CERTAINTY_CODES[certainty],
        evidence=(evidence,),
        provenance="handover_extractor_v2.3",
    )


def _restore_open_event_v23(
    item: Any,
    *,
    registry: CompactSourceRegistry,
) -> HandoverOpenEvent:
    if not isinstance(item, list) or len(item) != 3:
        raise ValueError("invalid_open_event_shape")
    source_index, quote, status = item
    if status not in _COMPLETION_CODES:
        raise ValueError("invalid_completion_status")
    evidence, entry = _evidence_from_quote(registry, source_index, quote)
    return HandoverOpenEvent(
        event_id=f"v23-open-{sha256_json(item)[:16]}",
        actors=(),
        action="",
        object="",
        completion_status=_COMPLETION_CODES[status],
        evidence=(evidence,),
        source_hash=entry.source.source_hash,
    )


def restore_and_validate_v23(
    payload: Mapping[str, Any],
    *,
    registry: CompactSourceRegistry,
    next_boundary: HandoverNextBoundary,
    stale_completed_claim_hashes: Sequence[str] = (),
) -> HandoverValidationResult:
    try:
        compact = CompactHandoverPayloadV23.model_validate(payload)
    except ValidationError as error:
        raise ValueError("InvalidCompactHandoverPayload") from error
    return _restore_and_validate(
        compact,
        registry=registry,
        next_boundary=next_boundary,
        stale_completed_claim_hashes=stale_completed_claim_hashes,
        claim_restorer=_restore_claim_v23,
        open_event_restorer=_restore_open_event_v23,
        arc_restorer=_restore_arc_progress_v22,
    )


def prompt_example_premise_v23() -> str:
    """The hypothetical source text the V2.3 prompt's worked example cites."""
    return "林晚放下相机，仍在等待周野的回应。"


def prompt_example_payload_v23() -> dict[str, Any]:
    """The exact worked example embedded in HANDOVER_EXTRACTION_PROMPT_V23.

    与 Prompt 内嵌文本的逐字一致性、示例经真实 parser 端到端全收，
    均由工程 gate 强制（V2.2 批次制度化的经验）。
    """
    return {
        "v": "2.3",
        "s": [[0, "林晚放下相机", "cs", "c", "c"]],
        "o": [[0, "林晚放下相机，仍在等待周野的回应", "o"]],
        "f": [],
        "a": [],
    }


def typical_compact_payload_v23() -> dict[str, Any]:
    return {
        "v": "2.3",
        "s": [[0, "林晚决定继续记录见闻", "cs", "c", "c"]],
        "o": [[0, "林晚等待周野回应邀请", "o"]],
        "f": [[0, "相册被留在书店里", "kf", "c", "c"]],
        "a": [[3, 0, "她翻开了那本相册", "p"]],
    }


def worst_legal_compact_payload_v23() -> dict[str, Any]:
    # 每 item 携带最长 20 字短引——V2.3 的最坏合法形状（短语层已退役）。
    quote = "引" * MAX_QUOTE_CHARS
    return {
        "v": "2.3",
        "s": [[0, quote, "cs", "c", "c"] for _ in range(MAX_END_STATES)],
        "o": [[0, quote, "o"] for _ in range(MAX_OPEN_EVENTS)],
        "f": [[0, quote, "kf", "c", "c"] for _ in range(MAX_NEW_FACTS)],
        "a": [[3, 0, quote, "p"] for _ in range(MAX_ARC_PROGRESS)],
    }
