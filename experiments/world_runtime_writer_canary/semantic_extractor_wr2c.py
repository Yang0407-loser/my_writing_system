"""WR2-C semantic extraction contract, prompt, and fail-closed parser.

The model may only propose evidence-bound state changes.  It never decides
transition legality and it cannot commit state; the deterministic WR2-B
validator remains the sole legality consumer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2a import EvidenceSpan
from experiments.world_runtime_writer_canary.delta_shadow_wr2b import (
    ChangeTypeV2,
    ProposedChangeV2,
    ProposedTypedDeltaV2,
)


SEMANTIC_EXTRACTOR_VERSION = "world-runtime-semantic-extractor-wr2c-v1"
MAX_EVENTS = 24
EventMode = Literal["actual", "planned", "conditional", "hearsay", "fictional", "dreamed", "negated"]
EventEpistemic = Literal["asserted", "unknown", "conflicted"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RawEvidence(FrozenModel):
    excerpt: str = Field(min_length=1, max_length=240)
    occurrence: int = Field(default=1, ge=1)


class RawSemanticEvent(FrozenModel):
    change_type: ChangeTypeV2
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    after_value: Any
    actor: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    event_id: str | None = None
    mode: EventMode
    epistemic: EventEpistemic
    evidence: tuple[RawEvidence, ...] = Field(min_length=1, max_length=4)


class RawSemanticResponse(FrozenModel):
    events: tuple[RawSemanticEvent, ...] = Field(max_length=MAX_EVENTS)


class DroppedEvent(FrozenModel):
    index: int = Field(ge=0)
    reason: str
    change_type: str | None = None


class SemanticExtractionArtifact(FrozenModel):
    extractor_version: Literal[SEMANTIC_EXTRACTOR_VERSION] = SEMANTIC_EXTRACTOR_VERSION
    sample_id: str
    output_hash: str
    raw_event_count: int = Field(ge=0)
    projected_event_count: int = Field(ge=0)
    dropped_events: tuple[DroppedEvent, ...] = ()
    delta: ProposedTypedDeltaV2
    model_calls: Literal[1] = 1
    state_mutated: Literal[False] = False
    commit_sink: Literal["forbidden"] = "forbidden"


ONTOLOGY_GUIDE = """Canonical change vocabulary:
- storefront_public_sale | bakery:wild-bread:storefront | public_sale_event | occurred | cash_exchange or digital_payment_exchange
- storefront_public_handoff | bakery:wild-bread:storefront | public_goods_handoff | occurred | free_handoff
- knowledge_state | character:* | article_knowledge | perceived | explicit_group_send_and_body_response, group_file_send_and_body_response, private_link_send_and_body_response, or missing_transmission_path
- resignation_acknowledgement | company:lin-wan | resignation_acknowledged | true | institutional_reply
- unsourced_project_fact | new persistent entity/relationship | identity_role or communication_recipient | asserted value | text_assertion
- object_state | object:* | content_state, temperature_state, or location_state | new state | explicit actor/natural mechanism, or missing_actor_or_event
- repeated_completed_event | article:lin-wan | publication_event | repeated | explicit_repeat_marker
- employment_state | employment:lin-wan | status | ended | acknowledged_effective_resignation or self_assumed_effective
- publication_state | article:lin-wan | publication_state | published | submit_and_platform_publish
- resignation_delivery | resignation:lin-wan | lifecycle_state | delivered | institutional_email_delivery
- resignation_personal_record | resignation:lin-wan | personal_record_state | saved | private_email_copy
- clock_state | world_clock | time | HH:MM | explicit_time_progression
- location_state | character:* | location | canonical location ID | explicit_entry
"""


SYSTEM_PROMPT = """You extract world-state transition proposals from Chinese fiction.
Return one JSON object only. Do not judge whether a transition is legal. An
illegal event that actually occurs in the prose must still be extracted. Never
invent an event, actor, channel, result, or evidence quote."""


USER_PROMPT = """Extract durable world-state changes from FINAL_TEXT.

CURRENT_STATE (facts before the scene; use only to understand before/after):
{state_json}

ONTOLOGY:
{ontology}

FINAL_TEXT:
{text}

Return exactly:
{{"events":[{{
  "change_type":"one ontology type",
  "subject":"canonical ID",
  "predicate":"canonical predicate",
  "after_value":"typed value",
  "actor":"canonical actor ID or narrator/unknown",
  "mechanism":"canonical mechanism",
  "event_id":null,
  "mode":"actual|planned|conditional|hearsay|fictional|dreamed|negated",
  "epistemic":"asserted|unknown|conflicted",
  "evidence":[{{"excerpt":"shortest exact continuous quote from FINAL_TEXT","occurrence":1}}]
}}]}}

Rules:
1. Extract only durable changes, not atmosphere, description with no change, UI checks, or repeated reminders of the before-state.
2. mode=actual only when the event happens in the current scene. Plans, conditions, hearsay, fiction-within-fiction, dreams and explicitly negated events must use their own mode.
3. epistemic=asserted only for what the narration directly supports. Keep explicit uncertainty as unknown/conflicted.
4. Evidence must be exact continuous text copied from FINAL_TEXT. Use 2-4 excerpts when transmission, perception or prerequisites are separated.
5. Do not omit an actual change merely because it violates CURRENT_STATE or seems unrealistic; the Validator decides legality.
6. Seeing a title/status/screenshot is not body perception. Sending to a private mailbox is not institutional delivery. A colleague's premise is not employment termination.
7. Do not include expected_validation, outcome, rule_ids, confidence, explanation, or prose outside JSON.
8. Return {{"events":[]}} when no durable change occurs.
"""


_FIXED_SHAPES: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "storefront_public_sale": ("bakery:wild-bread:storefront", ("public_sale_event",)),
    "storefront_public_handoff": ("bakery:wild-bread:storefront", ("public_goods_handoff",)),
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


def _state_payload(state) -> dict[str, Any]:
    return {
        "project_id": state.project_id,
        "revision": state.revision,
        "facts": [
            {
                "fact_id": item.fact_id,
                "subject": item.subject,
                "predicate": item.predicate,
                "value": item.value,
                "epistemic_status": item.epistemic_status,
            }
            for item in state.facts
        ],
    }


def build_messages(*, text: str, state_variant: str) -> list[dict[str, str]]:
    _, states, _ = wr1r._artifacts()
    state = states[state_variant]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_PROMPT.format(
                state_json=json.dumps(_state_payload(state), ensure_ascii=False, separators=(",", ":")),
                ontology=ONTOLOGY_GUIDE,
                text=text,
            ),
        },
    ]


def _find_span(text: str, evidence: RawEvidence) -> tuple[int, int] | None:
    cursor = 0
    start = -1
    for _ in range(evidence.occurrence):
        start = text.find(evidence.excerpt, cursor)
        if start < 0:
            return None
        cursor = start + len(evidence.excerpt)
    return start, start + len(evidence.excerpt)


def _shape_valid(event: RawSemanticEvent) -> bool:
    expected_subject, predicates = _FIXED_SHAPES[event.change_type]
    if expected_subject is not None and event.subject != expected_subject:
        return False
    if event.predicate not in predicates:
        return False
    if event.change_type == "knowledge_state" and not event.subject.startswith("character:"):
        return False
    if event.change_type == "object_state" and not event.subject.startswith("object:"):
        return False
    if event.change_type == "location_state" and not event.subject.startswith("character:"):
        return False
    return True


def _prior(event: RawSemanticEvent, state) -> tuple[Any, Literal["confirmed_true", "unknown"]]:
    if event.change_type in {"storefront_public_sale", "storefront_public_handoff", "unsourced_project_fact"}:
        return None, "unknown"
    if event.change_type == "repeated_completed_event":
        return "completed", "confirmed_true"
    fact = next(
        (
            item for item in state.facts
            if item.subject == event.subject and item.predicate == event.predicate
        ),
        None,
    )
    if fact is None:
        return None, "unknown"
    if fact.epistemic_status == "unknown":
        return None, "unknown"
    return fact.value, "confirmed_true"


def parse_semantic_response(
    *,
    text: str,
    response_text: str,
    sample_id: str,
    scene_id: str,
    state_variant: str,
    base_revision: int | None = None,
) -> SemanticExtractionArtifact:
    """Parse one model response; malformed or ungrounded events fail closed."""

    try:
        payload = json.loads(response_text)
        raw = RawSemanticResponse.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("wr2c_invalid_semantic_response") from exc

    _, states, _ = wr1r._artifacts()
    state = states[state_variant]
    revision = state.revision if base_revision is None else base_revision
    evidence_spans: list[EvidenceSpan] = []
    changes: list[ProposedChangeV2] = []
    dropped: list[DroppedEvent] = []
    signatures: set[tuple[str, str, str, str, str]] = set()

    for index, event in enumerate(raw.events):
        if event.mode != "actual" or event.epistemic != "asserted":
            dropped.append(DroppedEvent(index=index, reason=f"non_actual_or_non_asserted:{event.mode}:{event.epistemic}", change_type=event.change_type))
            continue
        if not _shape_valid(event):
            dropped.append(DroppedEvent(index=index, reason="ontology_shape_mismatch", change_type=event.change_type))
            continue
        resolved: list[tuple[RawEvidence, tuple[int, int]]] = []
        for evidence in event.evidence:
            span = _find_span(text, evidence)
            if span is None:
                resolved = []
                break
            resolved.append((evidence, span))
        if not resolved:
            dropped.append(DroppedEvent(index=index, reason="evidence_not_found", change_type=event.change_type))
            continue
        signature = (
            event.change_type,
            event.subject,
            event.predicate,
            json.dumps(event.after_value, ensure_ascii=False, sort_keys=True),
            event.mechanism,
        )
        if signature in signatures:
            dropped.append(DroppedEvent(index=index, reason="duplicate_semantic_change", change_type=event.change_type))
            continue
        signatures.add(signature)
        event_evidence_ids = []
        for evidence_index, (evidence, span) in enumerate(resolved, 1):
            evidence_id = f"ev:wr2c:{sample_id.lower()}:{index + 1}:{evidence_index}"
            evidence_spans.append(
                EvidenceSpan(
                    evidence_id=evidence_id,
                    claim=f"semantic extraction for {event.change_type}",
                    start=span[0],
                    end=span[1],
                    excerpt=evidence.excerpt,
                )
            )
            event_evidence_ids.append(evidence_id)
        before_value, before_status = _prior(event, state)
        changes.append(
            ProposedChangeV2(
                change_id=f"change:wr2c:{sample_id.lower()}:{len(changes) + 1}",
                sequence=len(changes) + 1,
                change_type=event.change_type,
                subject=event.subject,
                predicate=event.predicate,
                before_value=before_value,
                before_epistemic_status=before_status,
                after_value=event.after_value,
                actor=event.actor,
                mechanism=event.mechanism,
                event_id=event.event_id,
                evidence_ids=tuple(event_evidence_ids),
            )
        )

    output_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    delta = ProposedTypedDeltaV2(
        delta_id=f"delta:wr2c:{sample_id.lower()}",
        sample_id=sample_id,
        scene_id=scene_id,
        project_id="project:saturday-bakery",
        state_variant=state_variant,
        base_revision=revision,
        output_hash=output_hash,
        evidence=tuple(evidence_spans),
        changes=tuple(changes),
    )
    return SemanticExtractionArtifact(
        sample_id=sample_id,
        output_hash=output_hash,
        raw_event_count=len(raw.events),
        projected_event_count=len(changes),
        dropped_events=tuple(dropped),
        delta=delta,
    )
