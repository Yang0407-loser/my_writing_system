"""Evidence-backed subsection handover contract.

V2 deliberately keeps extraction, validation, and legacy adaptation separate.
The model may propose claims, but only locally validated claims can reach the
legacy consumers.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


HANDOVER_CONTRACT_V2_VERSION = "handover-contract-v2"
MAX_EVIDENCE_EXCERPT = 140

SourceType = Literal[
    "generated_subsection",
    "current_outline",
    "next_outline",
    "arc_milestone",
]
ClaimCategory = Literal[
    "time_state",
    "location_state",
    "character_state",
    "relationship_state",
    "known_fact",
    "object_or_resource_state",
    "foreshadow_state",
]
TemporalStatus = Literal["current", "past", "planned", "conditional", "unknown"]
Certainty = Literal["confirmed", "explicit_unknown"]
CompletionStatus = Literal[
    "open",
    "partially_completed",
    "completed",
    "unknown",
]
RejectionReason = Literal[
    "missing_source",
    "source_hash_mismatch",
    "invalid_evidence_span",
    "evidence_text_mismatch",
    "unsupported_psychology",
    "tense_or_state_mismatch",
    "stale_completed_event",
    "missing_arc_milestone_source",
    "invalid_category",
    "invalid_contract_shape",
]


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _normalize(value: str) -> str:
    return re.sub(r"[\s，。！？；：、“”‘’（）()\[\]{}<>《》—…,.!?;:'\"]+", "", value or "")


def _outline_text(value: Mapping[str, Any] | None) -> str:
    value = value or {}
    lines = [
        f"title: {str(value.get('title') or '').strip()}",
        f"description: {str(value.get('description') or '').strip()}",
    ]
    for index, item in enumerate(value.get("key_points") or [], 1):
        lines.append(f"key_point_{index}: {str(item).strip()}")
    return "\n".join(lines)


class HandoverSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: SourceType
    source_id: str
    source_hash: str
    text: str = Field(repr=False)

    @classmethod
    def create(
        cls,
        *,
        source_type: SourceType,
        source_id: str,
        text: str,
    ) -> "HandoverSource":
        return cls(
            source_type=source_type,
            source_id=source_id,
            source_hash=sha256_text(text),
            text=text,
        )

    def public_manifest(self) -> dict[str, str]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_hash": self.source_hash,
        }


class HandoverEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: SourceType
    source_id: str
    source_hash: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    excerpt: str = Field(min_length=1, max_length=MAX_EVIDENCE_EXCERPT)


class HandoverClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    category: ClaimCategory
    subject: str
    predicate: str
    object: str = ""
    temporal_status: TemporalStatus
    certainty: Certainty
    evidence: tuple[HandoverEvidence, ...] = ()
    claim_hash: str = ""
    provenance: str = "handover_extractor_v2"

    def with_hash(self) -> "HandoverClaim":
        payload = self.model_dump(mode="json", exclude={"claim_hash"})
        return self.model_copy(update={"claim_hash": sha256_json(payload)})


class HandoverEndState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: tuple[HandoverClaim, ...] = ()


class HandoverOpenEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    actors: tuple[str, ...] = ()
    action: str
    object: str = ""
    completion_status: CompletionStatus
    evidence: tuple[HandoverEvidence, ...] = ()
    source_hash: str


class HandoverNextBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    next_section: int | None = None
    next_subsection: int | None = None
    next_title: str = ""
    allowed_start_events: tuple[str, ...] = ()
    must_not_repeat_events: tuple[str, ...] = ()
    stop_or_transition_reason: str
    source_id: str
    source_hash: str
    provenance: str = "deterministic_outline_compilation"
    boundary_status: Literal["available", "conflicted", "section_end"] = "available"
    conflict_reasons: tuple[str, ...] = ()
    next_boundary_unavailable: str | None = None


class HandoverArcProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    character_id: str
    event_id: str
    completion_status: Literal["completed", "partially_completed", "unknown"]
    milestone_source_id: str
    milestone_source_hash: str
    evidence: tuple[HandoverEvidence, ...] = ()


class HandoverContractV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = HANDOVER_CONTRACT_V2_VERSION
    end_state: HandoverEndState = Field(default_factory=HandoverEndState)
    open_events: tuple[HandoverOpenEvent, ...] = ()
    next_boundary: HandoverNextBoundary
    arc_progress: tuple[HandoverArcProgress, ...] = ()
    source_manifest: tuple[dict[str, str], ...] = ()

    @property
    def contract_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class HandoverRejection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_type: Literal["claim", "open_event", "arc_progress", "contract"]
    item_id: str
    rejection_reason: RejectionReason


class HandoverValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: HandoverContractV2
    rejections: tuple[HandoverRejection, ...] = ()
    accepted_claim_count: int = Field(ge=0)
    rejected_claim_count: int = Field(ge=0)
    rejection_counts: dict[str, int] = Field(default_factory=dict)
    source_traceability_rate: float = Field(ge=0, le=1)


def build_handover_sources(
    *,
    section: int,
    subsection: int,
    generated_text: str,
    current_outline: Mapping[str, Any] | None,
    next_outline: Mapping[str, Any] | None,
    arc_milestones: Sequence[Any] = (),
) -> dict[str, HandoverSource]:
    sources: list[HandoverSource] = [
        HandoverSource.create(
            source_type="generated_subsection",
            source_id=f"generated-subsection:S{section}.{subsection}",
            text=generated_text,
        ),
        HandoverSource.create(
            source_type="current_outline",
            source_id=f"outline-current:S{section}.{subsection}",
            text=_outline_text(current_outline),
        ),
    ]
    if next_outline is not None:
        next_section = int(next_outline.get("_section", section))
        next_subsection = int(next_outline.get("subsection", subsection + 1))
        sources.append(
            HandoverSource.create(
                source_type="next_outline",
                source_id=f"outline-next:S{next_section}.{next_subsection}",
                text=_outline_text(next_outline),
            )
        )
    for item in arc_milestones:
        event_id = str(getattr(item, "event_id", "") or "")
        source_id = str(getattr(item, "source_id", "") or "")
        source_hash = str(getattr(item, "source_hash", "") or "")
        description = str(getattr(item, "description", "") or "")
        if not event_id or not source_id or not source_hash or not description:
            continue
        sources.append(
            HandoverSource(
                source_type="arc_milestone",
                source_id=source_id,
                source_hash=source_hash,
                text=description,
            )
        )
    return {item.source_id: item for item in sources}


def _events_from_outline(value: Mapping[str, Any] | None) -> tuple[str, ...]:
    value = value or {}
    points = tuple(
        str(item).strip()
        for item in (value.get("key_points") or [])
        if str(item).strip()
    )
    if points:
        return points
    description = str(value.get("description") or "").strip()
    if description:
        return (description,)
    title = str(value.get("title") or "").strip()
    return (title,) if title else ()


def compile_next_boundary(
    *,
    section: int,
    subsection: int,
    current_outline: Mapping[str, Any] | None,
    next_outline: Mapping[str, Any] | None,
) -> HandoverNextBoundary:
    current_source = HandoverSource.create(
        source_type="current_outline",
        source_id=f"outline-current:S{section}.{subsection}",
        text=_outline_text(current_outline),
    )
    if next_outline is None:
        return HandoverNextBoundary(
            stop_or_transition_reason="current subsection is the section end",
            source_id=current_source.source_id,
            source_hash=current_source.source_hash,
            boundary_status="section_end",
            next_boundary_unavailable="section_end",
        )

    next_section = int(next_outline.get("_section", section))
    next_subsection = int(next_outline.get("subsection", subsection + 1))
    next_source = HandoverSource.create(
        source_type="next_outline",
        source_id=f"outline-next:S{next_section}.{next_subsection}",
        text=_outline_text(next_outline),
    )
    current_events = _events_from_outline(current_outline)
    next_events = _events_from_outline(next_outline)
    current_norm = {_normalize(item) for item in current_events if _normalize(item)}
    repeated = tuple(
        item for item in next_events if _normalize(item) in current_norm
    )
    conflicts = (
        ("next_outline_repeats_current_completed_event",) if repeated else ()
    )
    combined_hash = sha256_json(
        {
            "current": current_source.public_manifest(),
            "next": next_source.public_manifest(),
        }
    )
    return HandoverNextBoundary(
        next_section=next_section,
        next_subsection=next_subsection,
        next_title=str(next_outline.get("title") or ""),
        allowed_start_events=next_events,
        must_not_repeat_events=current_events,
        stop_or_transition_reason=(
            "transition to next outline without repeating completed current events"
        ),
        source_id=f"{current_source.source_id}+{next_source.source_id}",
        source_hash=combined_hash,
        boundary_status="conflicted" if conflicts else "available",
        conflict_reasons=conflicts,
    )


_PSYCHOLOGY_TERMS = (
    "意识到",
    "感受到",
    "内心",
    "动摇",
    "好奇",
    "理解加深",
    "被接纳",
    "精神坐标",
    "无言陪伴",
)
_NON_CURRENT_MARKERS = (
    "计划",
    "打算",
    "如果",
    "若",
    "将会",
    "以后",
    "曾经",
    "回忆",
    "没有",
    "未曾",
    "并未",
)


def _validate_evidence(
    evidence: Sequence[HandoverEvidence],
    sources: Mapping[str, HandoverSource],
) -> RejectionReason | None:
    if not evidence:
        return "missing_source"
    for item in evidence:
        source = sources.get(item.source_id)
        if source is None or source.source_type != item.source_type:
            return "missing_source"
        if source.source_hash != item.source_hash:
            return "source_hash_mismatch"
        if not 0 <= item.start < item.end <= len(source.text):
            return "invalid_evidence_span"
        if source.text[item.start:item.end] != item.excerpt:
            return "evidence_text_mismatch"
        if len(item.excerpt) > MAX_EVIDENCE_EXCERPT:
            return "invalid_evidence_span"
    return None


def _claim_rejection(
    claim: HandoverClaim,
    sources: Mapping[str, HandoverSource],
    stale_completed_claim_hashes: set[str],
) -> RejectionReason | None:
    evidence_error = _validate_evidence(claim.evidence, sources)
    if evidence_error:
        return evidence_error
    claim_text = f"{claim.subject}{claim.predicate}{claim.object}"
    evidence_text = "".join(item.excerpt for item in claim.evidence)
    for component in (claim.subject, claim.predicate, claim.object):
        if component and _normalize(component) not in _normalize(evidence_text):
            return (
                "unsupported_psychology"
                if any(term in claim_text for term in _PSYCHOLOGY_TERMS)
                else "evidence_text_mismatch"
            )
    if any(term in claim_text for term in _PSYCHOLOGY_TERMS):
        if not all(term in evidence_text for term in _PSYCHOLOGY_TERMS if term in claim_text):
            return "unsupported_psychology"
    if (
        claim.temporal_status == "current"
        and any(marker in evidence_text for marker in _NON_CURRENT_MARKERS)
    ):
        return "tense_or_state_mismatch"
    if claim.with_hash().claim_hash in stale_completed_claim_hashes:
        return "stale_completed_event"
    return None


def _parse_items(
    raw: Any,
    model: type[BaseModel],
    item_type: Literal["claim", "open_event", "arc_progress"],
) -> tuple[list[BaseModel], list[HandoverRejection]]:
    accepted: list[BaseModel] = []
    rejected: list[HandoverRejection] = []
    if raw in (None, ""):
        return accepted, rejected
    if not isinstance(raw, list):
        return accepted, [
            HandoverRejection(
                item_type=item_type,
                item_id="payload",
                rejection_reason="invalid_contract_shape",
            )
        ]
    for index, item in enumerate(raw):
        try:
            accepted.append(model.model_validate(item))
        except ValidationError:
            identifier = (
                str(item.get("claim_id") or item.get("event_id") or index)
                if isinstance(item, Mapping)
                else str(index)
            )
            rejected.append(
                HandoverRejection(
                    item_type=item_type,
                    item_id=identifier,
                    rejection_reason="invalid_contract_shape",
                )
            )
    return accepted, rejected


class HandoverContractValidatorV2:
    def validate(
        self,
        raw: Mapping[str, Any],
        *,
        sources: Mapping[str, HandoverSource],
        next_boundary: HandoverNextBoundary,
        stale_completed_claim_hashes: Sequence[str] = (),
    ) -> HandoverValidationResult:
        raw = dict(raw) if isinstance(raw, Mapping) else {}
        raw_claims = raw.get("claims")
        if raw_claims is None and isinstance(raw.get("end_state"), Mapping):
            raw_claims = raw["end_state"].get("claims")
        claims, rejections = _parse_items(raw_claims, HandoverClaim, "claim")
        events, event_rejections = _parse_items(
            raw.get("open_events"), HandoverOpenEvent, "open_event"
        )
        arcs, arc_rejections = _parse_items(
            raw.get("arc_progress"), HandoverArcProgress, "arc_progress"
        )
        rejections.extend(event_rejections)
        rejections.extend(arc_rejections)

        accepted_claims: list[HandoverClaim] = []
        stale = set(stale_completed_claim_hashes)
        for value in claims:
            claim = value.with_hash()
            reason = _claim_rejection(claim, sources, stale)
            if reason:
                rejections.append(
                    HandoverRejection(
                        item_type="claim",
                        item_id=claim.claim_id,
                        rejection_reason=reason,
                    )
                )
            else:
                accepted_claims.append(claim)

        accepted_events: list[HandoverOpenEvent] = []
        for value in events:
            reason = _validate_evidence(value.evidence, sources)
            if (
                reason is None
                and value.evidence
                and value.source_hash != value.evidence[0].source_hash
            ):
                reason = "source_hash_mismatch"
            if reason:
                rejections.append(
                    HandoverRejection(
                        item_type="open_event",
                        item_id=value.event_id,
                        rejection_reason=reason,
                    )
                )
                continue
            if value.completion_status == "completed":
                rejections.append(
                    HandoverRejection(
                        item_type="open_event",
                        item_id=value.event_id,
                        rejection_reason="stale_completed_event",
                    )
                )
                continue
            accepted_events.append(value)

        accepted_arcs: list[HandoverArcProgress] = []
        for value in arcs:
            milestone = sources.get(value.milestone_source_id)
            if (
                not value.event_id
                or milestone is None
                or milestone.source_type != "arc_milestone"
                or milestone.source_hash != value.milestone_source_hash
            ):
                rejections.append(
                    HandoverRejection(
                        item_type="arc_progress",
                        item_id=value.event_id or value.character_id,
                        rejection_reason="missing_arc_milestone_source",
                    )
                )
                continue
            reason = _validate_evidence(value.evidence, sources)
            if reason:
                rejections.append(
                    HandoverRejection(
                        item_type="arc_progress",
                        item_id=value.event_id,
                        rejection_reason=reason,
                    )
                )
                continue
            if value.completion_status == "unknown":
                continue
            accepted_arcs.append(value)

        contract = HandoverContractV2(
            end_state=HandoverEndState(claims=tuple(accepted_claims)),
            open_events=tuple(accepted_events),
            next_boundary=next_boundary,
            arc_progress=tuple(accepted_arcs),
            source_manifest=tuple(
                source.public_manifest() for source in sources.values()
            ),
        )
        counts = Counter(item.rejection_reason for item in rejections)
        accepted_evidence = [
            evidence
            for claim in accepted_claims
            for evidence in claim.evidence
        ] + [
            evidence
            for event in accepted_events
            for evidence in event.evidence
        ] + [
            evidence
            for arc in accepted_arcs
            for evidence in arc.evidence
        ]
        return HandoverValidationResult(
            contract=contract,
            rejections=tuple(rejections),
            accepted_claim_count=(
                len(accepted_claims) + len(accepted_events) + len(accepted_arcs)
            ),
            rejected_claim_count=len(rejections),
            rejection_counts=dict(counts),
            source_traceability_rate=1.0 if accepted_evidence else 1.0,
        )


def adapt_v2_to_legacy_handover_note(
    validation: HandoverValidationResult,
) -> dict[str, Any]:
    contract = validation.contract
    claims = contract.end_state.claims
    foreshadowing = [
        f"{item.subject}{item.predicate}{item.object}"
        for item in claims
        if item.category == "foreshadow_state"
        and item.certainty == "confirmed"
    ]
    character_states = [
        f"{item.subject}{item.predicate}{item.object}"
        for item in claims
        if item.category in {"character_state", "relationship_state"}
        and item.temporal_status == "current"
        and item.certainty == "confirmed"
    ]
    new_facts = [
        f"{item.subject}{item.predicate}{item.object}"
        for item in claims
        if item.category in {
            "time_state",
            "location_state",
            "known_fact",
            "object_or_resource_state",
        }
        and item.temporal_status == "current"
        and item.certainty == "confirmed"
    ]
    open_threads = [
        f"{item.action}{item.object}".strip()
        for item in contract.open_events
        if item.completion_status in {"open", "partially_completed"}
    ]
    arc_progress = {
        item.character_id: "done"
        for item in contract.arc_progress
        if item.completion_status == "completed"
    }
    contradictions = list(contract.next_boundary.conflict_reasons)
    return {
        "foreshadowing": "；".join(foreshadowing),
        "character_state": "；".join(character_states),
        "open_threads": "；".join(open_threads),
        "new_facts": new_facts,
        "found_contradictions": contradictions,
        "resolved_events": [],
        "arc_progress": arc_progress,
    }


def render_v2_prompt_context(
    sources: Mapping[str, HandoverSource],
    next_boundary: HandoverNextBoundary,
) -> dict[str, str]:
    generated = next(
        (item for item in sources.values() if item.source_type == "generated_subsection"),
        None,
    )
    current = next(
        (item for item in sources.values() if item.source_type == "current_outline"),
        None,
    )
    following = next(
        (item for item in sources.values() if item.source_type == "next_outline"),
        None,
    )
    arcs = [
        item for item in sources.values() if item.source_type == "arc_milestone"
    ]
    return {
        "section_text": generated.text[:3000] if generated else "",
        "generated_source": canonical_json(
            generated.public_manifest() if generated else {}
        ),
        "current_outline": current.text if current else "",
        "current_outline_source": canonical_json(
            current.public_manifest() if current else {}
        ),
        "next_outline": following.text if following else "",
        "next_outline_source": canonical_json(
            following.public_manifest() if following else {}
        ),
        "arc_sources": canonical_json(
            [item.public_manifest() | {"text": item.text} for item in arcs]
        ),
        "compiled_boundary": canonical_json(next_boundary.model_dump(mode="json")),
    }
