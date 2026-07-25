"""Compact transport for the evidence-backed handover V2 contract.

The model emits only source indexes, spans, compact enums, and short semantic
text. Authoritative provenance and the V2 contract are reconstructed locally.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..utils.llm_client import estimate_tokens
from .handover_contract_v2 import (
    HandoverClaim,
    HandoverContractValidatorV2,
    HandoverEvidence,
    HandoverNextBoundary,
    HandoverOpenEvent,
    HandoverRejection,
    HandoverSource,
    HandoverValidationResult,
    canonical_json,
    sha256_json,
)


HANDOVER_COMPACT_V21_VERSION = "2.1"
HANDOVER_COMPACT_V21_MAX_OUTPUT_TOKENS = 600
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
        text = source.text[:3000] if source.source_type == "generated_subsection" else source.text
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


def _rejection(item_type: str, item_id: str) -> HandoverRejection:
    return HandoverRejection(
        item_type=item_type,
        item_id=item_id,
        rejection_reason="invalid_contract_shape",
    )


def _compact_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > MAX_COMPACT_TEXT:
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

    raw: dict[str, list[Any]] = {"claims": [], "open_events": [], "arc_progress": []}
    local_rejections: list[HandoverRejection] = []
    for kind, items, allowed in (
        ("state", compact.s, _STATE_CATEGORIES),
        ("fact", compact.f, _FACT_CATEGORIES),
    ):
        for index, item in enumerate(items):
            try:
                raw["claims"].append(
                    _restore_claim(item, registry=registry, allowed_categories=allowed).model_dump(mode="json")
                )
            except (TypeError, ValueError, ValidationError):
                local_rejections.append(_rejection("claim", f"{kind}:{index}"))
    for index, item in enumerate(compact.o):
        try:
            raw["open_events"].append(
                _restore_open_event(item, registry=registry).model_dump(mode="json")
            )
        except (TypeError, ValueError, ValidationError):
            local_rejections.append(_rejection("open_event", f"open:{index}"))
    for index, item in enumerate(compact.a):
        try:
            raw["arc_progress"].append(
                _restore_arc_progress(item, registry=registry)
            )
        except (TypeError, ValueError, ValidationError):
            local_rejections.append(_rejection("arc_progress", f"arc:{index}"))

    validation = HandoverContractValidatorV2().validate(
        raw,
        sources={entry.source.source_id: entry.source for entry in registry.entries},
        next_boundary=next_boundary,
        stale_completed_claim_hashes=stale_completed_claim_hashes,
    )
    if not local_rejections:
        return validation
    rejections = (*local_rejections, *validation.rejections)
    counts = Counter(item.rejection_reason for item in rejections)
    return validation.model_copy(
        update={
            "rejections": rejections,
            "rejected_claim_count": len(rejections),
            "rejection_counts": dict(counts),
        }
    )


def compact_payload_metrics(payload: Mapping[str, Any]) -> dict[str, int]:
    encoded = canonical_json(payload)
    empty_wrapper = canonical_json({"v": "2.1", "s": [], "o": [], "f": [], "a": []})
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
    text = "人|" + "状" * (MAX_COMPACT_TEXT - 4) + "|物"
    return {
        "v": "2.1",
        "s": [[0, i, i + 1, "cs", "c", "c", text] for i in range(MAX_END_STATES)],
        "o": [[0, 10 + i, 11 + i, "o", text] for i in range(MAX_OPEN_EVENTS)],
        "f": [[0, 20 + i, 21 + i, "kf", "c", "c", text] for i in range(MAX_NEW_FACTS)],
        "a": [[3, 0, 30 + i, 31 + i, "p"] for i in range(MAX_ARC_PROGRESS)],
    }
