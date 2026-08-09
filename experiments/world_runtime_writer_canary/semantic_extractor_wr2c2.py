"""WR2-C2 semantic proposer with a deterministic canonical projection layer.

The language model proposes evidence-bound events.  This module owns the
canonical fields that can be derived locally, removes no-op state proposals,
and freezes object-change granularity before passing the delta to the frozen
WR2-B Validator.  It never decides legality or commits state.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import ConfigDict, Field

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2a import EvidenceSpan
from experiments.world_runtime_writer_canary.delta_shadow_wr2b import (
    ProposedChangeV2,
    ProposedTypedDeltaV2,
)
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c import (
    DroppedEvent,
    FrozenModel,
    ONTOLOGY_GUIDE,
    RawEvidence,
    RawSemanticEvent,
    RawSemanticResponse,
    _find_span,
    _shape_valid,
    _state_payload,
)


SEMANTIC_EXTRACTOR_VERSION = "world-runtime-semantic-extractor-wr2c2-v1"


class SemanticExtractionArtifactC2(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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


SYSTEM_PROMPT = """You extract world-state transition proposals from Chinese fiction.
Return one JSON object only. Do not judge whether a transition is legal. An
illegal event that actually occurs in the prose must still be extracted. Never
invent an event, actor, channel, result, or evidence quote. Canonical IDs and
mechanism aliases are normalized locally after your response."""


WORKED_EXAMPLES = """Three ontology reminders (examples are not from this task):
- "新来的排班员韩梅让店员换班" introduces a durable role/entity, so propose
  unsourced_project_fact; do not silently treat a newly named role as canon.
- If state says an upload already completed and prose says the character presses
  upload again, propose repeated_completed_event even though it is illegal.
- "陈青推门进入库房" changes Chen Qing's location; propose location_state.
  Merely looking toward the door or planning to enter is not a location change.
For an object described as both cleaned and stored without any causal event,
represent the composite result as content_state=clean_and_stored, not as two
independent empty/location changes."""


USER_PROMPT = """Extract durable world-state changes from FINAL_TEXT.

CURRENT_STATE (facts before the scene; use only to understand before/after):
{state_json}

ONTOLOGY:
{ontology}

WORKED EXAMPLES:
{examples}

FINAL_TEXT:
{text}

Return exactly:
{{"events":[{{
  "change_type":"one ontology type",
  "subject":"canonical ID",
  "predicate":"canonical predicate",
  "after_value":"typed value",
  "actor":"actor described by the prose, or narrator/unknown",
  "mechanism":"ontology mechanism or a short semantic alias",
  "event_id":null,
  "mode":"actual|planned|conditional|hearsay|fictional|dreamed|negated",
  "epistemic":"asserted|unknown|conflicted",
  "evidence":[{{"excerpt":"shortest exact continuous quote from FINAL_TEXT","occurrence":1}}]
}}]}}

Rules:
1. Extract only durable changes, not atmosphere, UI checks, repeated reminders
   of before-state, or a value that remains identical to CURRENT_STATE.
2. mode=actual only when the event happens in the current scene. Plans,
   conditions, hearsay, fiction-within-fiction, dreams and explicitly negated
   events must use their own mode.
3. epistemic=asserted only for what narration directly supports. Keep explicit
   uncertainty as unknown/conflicted.
4. Evidence must be exact continuous text copied from FINAL_TEXT. Use 2-4
   excerpts when transmission, perception or prerequisites are separated.
5. Do not omit an actual change because it violates CURRENT_STATE; the Validator
   alone decides legality.
6. Seeing a title/status/screenshot is not body perception. Sending to a private
   mailbox is not institutional delivery. A colleague's premise is not
   employment termination.
7. Do not include expected_validation, outcome, rule_ids, confidence,
   explanation, or prose outside JSON.
8. Return {{"events":[]}} when no durable change occurs.
"""


def build_messages(*, text: str, state_variant: str) -> list[dict[str, str]]:
    _, states, _ = wr1r._artifacts()
    state = states[state_variant]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_PROMPT.format(
                state_json=json.dumps(
                    _state_payload(state), ensure_ascii=False, separators=(",", ":")
                ),
                ontology=ONTOLOGY_GUIDE,
                examples=WORKED_EXAMPLES,
                text=text,
            ),
        },
    ]


_MECHANISM_ALIASES = {
    "explicit actor": "actor_pours_out",
    "explicit_actor": "actor_pours_out",
    "actor pours out": "actor_pours_out",
    "institutional response": "institutional_reply",
    "institutional acknowledgement": "institutional_reply",
    "no actor or event": "missing_actor_or_event",
    "missing actor or event": "missing_actor_or_event",
}


def _canonicalize_event(event: RawSemanticEvent) -> RawSemanticEvent:
    """Normalize only fields whose authority belongs to the local projector."""

    mechanism = _MECHANISM_ALIASES.get(event.mechanism.strip().lower(), event.mechanism)
    actor = event.actor
    if event.change_type == "resignation_acknowledgement" and mechanism == "institutional_reply":
        actor = "company:hr-system"
    if (
        event.change_type == "object_state"
        and event.predicate == "content_state"
        and event.after_value == "empty"
        and actor not in {"unknown", "narrator"}
        and mechanism in {"explicit", "explicit_action"}
    ):
        mechanism = "actor_pours_out"
    return event.model_copy(update={"actor": actor, "mechanism": mechanism})


def _evidence_text(event: RawSemanticEvent) -> str:
    return "".join(item.excerpt for item in event.evidence)


def _coalesce_object_events(
    events: list[tuple[int, RawSemanticEvent]],
) -> tuple[list[tuple[int, RawSemanticEvent]], list[DroppedEvent]]:
    """Collapse a model-split clean-and-store result into one atomic change."""

    consumed: set[int] = set()
    replacements: dict[int, RawSemanticEvent] = {}
    dropped: list[DroppedEvent] = []
    for content_pos, (content_index, content) in enumerate(events):
        if (
            content.change_type != "object_state"
            or content.predicate != "content_state"
            or content.after_value not in {"empty", "clean"}
        ):
            continue
        for location_pos, (location_index, location) in enumerate(events):
            if location_pos == content_pos or location.subject != content.subject:
                continue
            if location.change_type != "object_state" or location.predicate != "location_state":
                continue
            evidence_text = _evidence_text(content) + _evidence_text(location)
            stored = any(token in str(location.after_value).lower() for token in ("cabinet", "cupboard", "橱柜"))
            cleaned = any(token in evidence_text.lower() for token in ("clean", "washed", "洗净", "洗好", "冲洗"))
            missing_cause = content.mechanism == "missing_actor_or_event" or location.mechanism == "missing_actor_or_event"
            if not (stored and cleaned and missing_cause):
                continue
            combined_evidence = tuple(dict.fromkeys(content.evidence + location.evidence))
            replacements[content_index] = content.model_copy(
                update={
                    "after_value": "clean_and_stored",
                    "mechanism": "missing_actor_or_event",
                    "actor": "unknown",
                    "evidence": combined_evidence,
                }
            )
            consumed.add(location_index)
            dropped.append(
                DroppedEvent(
                    index=location_index,
                    reason="coalesced_into_object_composite",
                    change_type=location.change_type,
                )
            )
            break
    result = [
        (index, replacements.get(index, event))
        for index, event in events
        if index not in consumed
    ]
    return result, dropped


def _prior(event: RawSemanticEvent, state) -> tuple[Any, Literal["confirmed_true", "unknown"]]:
    if event.change_type in {
        "storefront_public_sale",
        "storefront_public_handoff",
        "unsourced_project_fact",
    }:
        return None, "unknown"
    if event.change_type == "repeated_completed_event":
        return "completed", "confirmed_true"
    state_predicate = "location" if (
        event.change_type == "object_state" and event.predicate == "location_state"
    ) else event.predicate
    fact = next(
        (
            item
            for item in state.facts
            if item.subject == event.subject and item.predicate == state_predicate
        ),
        None,
    )
    if fact is None or fact.epistemic_status == "unknown":
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
) -> SemanticExtractionArtifactC2:
    """Parse, canonicalize, state-diff, and project one semantic response."""

    try:
        raw = RawSemanticResponse.model_validate(json.loads(response_text))
    except Exception as exc:
        raise ValueError("wr2c2_invalid_semantic_response") from exc

    _, states, _ = wr1r._artifacts()
    state = states[state_variant]
    revision = state.revision if base_revision is None else base_revision
    dropped: list[DroppedEvent] = []
    eligible: list[tuple[int, RawSemanticEvent]] = []
    for index, raw_event in enumerate(raw.events):
        event = _canonicalize_event(raw_event)
        if event.mode != "actual" or event.epistemic != "asserted":
            dropped.append(DroppedEvent(
                index=index,
                reason=f"non_actual_or_non_asserted:{event.mode}:{event.epistemic}",
                change_type=event.change_type,
            ))
            continue
        if not _shape_valid(event):
            dropped.append(DroppedEvent(index=index, reason="ontology_shape_mismatch", change_type=event.change_type))
            continue
        eligible.append((index, event))
    eligible, coalesced = _coalesce_object_events(eligible)
    dropped.extend(coalesced)

    evidence_spans: list[EvidenceSpan] = []
    changes: list[ProposedChangeV2] = []
    signatures: set[tuple[str, str, str, str, str]] = set()
    for index, event in eligible:
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
        before_value, before_status = _prior(event, state)
        if before_status == "confirmed_true" and event.after_value == before_value:
            dropped.append(DroppedEvent(index=index, reason="no_state_change", change_type=event.change_type))
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
        event_evidence_ids: list[str] = []
        for evidence_index, (evidence, span) in enumerate(resolved, 1):
            evidence_id = f"ev:wr2c2:{sample_id.lower()}:{index + 1}:{evidence_index}"
            evidence_spans.append(EvidenceSpan(
                evidence_id=evidence_id,
                claim=f"semantic extraction for {event.change_type}",
                start=span[0],
                end=span[1],
                excerpt=evidence.excerpt,
            ))
            event_evidence_ids.append(evidence_id)
        changes.append(ProposedChangeV2(
            change_id=f"change:wr2c2:{sample_id.lower()}:{len(changes) + 1}",
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
        ))

    output_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    delta = ProposedTypedDeltaV2(
        delta_id=f"delta:wr2c2:{sample_id.lower()}",
        sample_id=sample_id,
        scene_id=scene_id,
        project_id="project:saturday-bakery",
        state_variant=state_variant,
        base_revision=revision,
        output_hash=output_hash,
        evidence=tuple(evidence_spans),
        changes=tuple(changes),
    )
    return SemanticExtractionArtifactC2(
        sample_id=sample_id,
        output_hash=output_hash,
        raw_event_count=len(raw.events),
        projected_event_count=len(changes),
        dropped_events=tuple(sorted(dropped, key=lambda item: item.index)),
        delta=delta,
    )
