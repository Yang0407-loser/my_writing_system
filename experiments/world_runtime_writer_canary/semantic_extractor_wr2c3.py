"""WR2-C3 semantic judgment contract and parser.

The model answers a per-type checklist with occurred / after_value / evidence.
It never outputs canonical subject, predicate, mechanism, actor or IDs; the
deterministic WR2-C3 projector owns all canonical fields.  Legality remains
with the frozen WR2-B validator, and nothing in this module can commit state.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import ConfigDict, Field, ValidationError

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2b import (
    ProposedTypedDeltaV2,
)
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c import (
    DroppedEvent,
    EventEpistemic,
    EventMode,
    FrozenModel,
    RawEvidence,
    _state_payload,
)
from experiments.world_runtime_writer_canary.semantic_projector_wr2c3 import (
    build_delta,
    project,
)


SEMANTIC_EXTRACTOR_VERSION = "world-runtime-semantic-extractor-wr2c3-v1"
MAX_JUDGMENTS = 32


class RawJudgment(FrozenModel):
    change_type: str = Field(min_length=1)
    occurred: bool
    after_value: Any = None
    mode: EventMode = "actual"
    epistemic: EventEpistemic = "asserted"
    evidence: tuple[RawEvidence, ...] = Field(default=(), max_length=4)


class RawJudgmentResponse(FrozenModel):
    judgments: tuple[RawJudgment, ...] = Field(min_length=1, max_length=MAX_JUDGMENTS)


class SemanticExtractionArtifactC3(FrozenModel):
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


SYSTEM_PROMPT = """You judge whether durable world-state changes occur in a
Chinese fiction excerpt. You answer one checklist item per ontology type.
You never output canonical subject/predicate/mechanism/actor/IDs; those are
resolved deterministically after your response. Do not judge legality: an
illegal event that actually occurs in the prose must still be judged as
occurred=true. Never invent an event or evidence quote."""


CHECKLIST = """JUDGE EVERY TYPE BELOW. Output exactly 13 judgments, one per type.
occurred=true only when the prose supports a durable change; otherwise
occurred=false, evidence=[], after_value=null.

1 storefront_public_sale: customer pays (QR code, scan, transfer, cash) and
  goods leave the storefront -> after_value "occurred"
2 storefront_public_handoff: goods are handed out without payment ->
  after_value "occurred"
3 knowledge_state: a character receives and perceives article body (group /
  file / link transmission plus quotation, reply or visible reading) ->
  after_value "perceived"
4 resignation_acknowledgement: HR system / HR email / company formally
  acknowledges the resignation -> after_value true
5 unsourced_project_fact: a new persistent role or relationship appears (e.g.
  "new procurement supervisor Han Bing") -> after_value the role word, e.g.
  "采购主管"
6 object_state: object content / temperature / location changes (poured out,
  washed and stored, moved) -> after_value the state word, e.g. "empty",
  "clean_and_stored", "cold", or "放回原位"
7 repeated_completed_event: an already-completed publish/upload action is
  executed again -> after_value "repeated"
8 employment_state: the employment relationship ends -> after_value "ended";
  if the prose also shows institutional acknowledgement, judge type 4 too
9 publication_state: article becomes publicly published after submission ->
  after_value "published"
10 resignation_delivery: resignation reaches the company HR channel ->
   after_value "delivered"
11 resignation_personal_record: resignation is only copied/saved to a private
   mailbox -> after_value "saved"
12 clock_state: prose explicitly advances the wall clock / scene time ->
   after_value the time, e.g. "05:40" or "五点四十"
13 location_state: a character explicitly enters a place (workshop, storeroom,
   storefront) -> after_value the place word, e.g. "操作间"

Chain rule: if employment ends through an acknowledged resignation, the text
must contain both type 4 and type 8 evidence; judge both.
"""


USER_PROMPT = """CURRENT_STATE (facts before the scene; use only to understand
before/after):
{state_json}

CHECKLIST:
{checklist}

FINAL_TEXT:
{text}

Return exactly:
{{"judgments":[
  {{"change_type":"one of the 13 types","occurred":true|false,
    "after_value":<typed value or null>,"mode":"actual",
    "epistemic":"asserted","evidence":[{{"excerpt":"shortest exact continuous
    quote from FINAL_TEXT","occurrence":1}}]}}
]}}

Rules:
1. All 13 types must appear exactly once.
2. occurred=true only for durable changes in this scene; plans, conditions,
   hearsay, fiction-within-fiction, dreams and negated events keep
   occurred=false. mode=actual, epistemic=asserted only when narration
   directly supports it.
3. Evidence must be exact continuous text copied from FINAL_TEXT. Use 2-4
   excerpts when transmission, perception or prerequisites are separated.
4. Do not skip an actual change because it violates CURRENT_STATE or seems
   illegal; the Validator decides legality.
5. Seeing a title/status/screenshot is not body perception. Sending to a
   private mailbox is not institutional delivery. A colleague's premise is not
   employment termination. Looking at a clock is not a clock change unless
   time advances; looking at a door is not entering.
6. Do not include subject, predicate, mechanism, actor, canonical ID,
   expected_validation, outcome, explanation, or prose outside JSON.
7. Return all 13 judgments even when no durable change occurs.
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
                checklist=CHECKLIST,
                text=text,
            ),
        },
    ]


def parse_semantic_response(
    *,
    text: str,
    response_text: str,
    sample_id: str,
    scene_id: str,
    state_variant: str,
    base_revision: int | None = None,
) -> SemanticExtractionArtifactC3:
    """Parse one judgment response, project it, and return the typed delta."""

    try:
        payload = json.loads(response_text)
        raw = RawJudgmentResponse.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("wr2c3_invalid_judgment_response") from exc

    _, states, _ = wr1r._artifacts()
    state = states[state_variant]
    revision = state.revision if base_revision is None else base_revision
    judgments = [item.model_dump(mode="json") for item in raw.judgments]
    events, dropped = project(text=text, state=state, judgments=judgments)
    delta, _ = build_delta(
        text=text,
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
        base_revision=revision,
        events=events,
    )
    output_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if delta.output_hash != output_hash:
        raise RuntimeError("wr2c3_output_hash_mismatch")
    return SemanticExtractionArtifactC3(
        sample_id=sample_id,
        output_hash=output_hash,
        raw_event_count=sum(item["occurred"] for item in judgments),
        projected_event_count=len(events),
        dropped_events=tuple(sorted(dropped, key=lambda item: item.index)),
        delta=delta,
    )
