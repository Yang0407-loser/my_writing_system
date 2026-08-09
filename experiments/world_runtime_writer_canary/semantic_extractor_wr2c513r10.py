"""WR2-C5.1.3-R10 semantic judgment contract and parser.

Same judgment+evidence contract as WR2-C5.1.3, with type 3 clarified: a
perception that appears impossible (e.g. the draft was never sent) is still
occurred=true; legality is decided locally.

Canonical fields remain fully owned by the WR2-C5.1.1 projector.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import ConfigDict, Field, ValidationError

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2c5 import ProposedTypedDeltaV5
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c import (
    DroppedEvent,
    EventEpistemic,
    EventMode,
    FrozenModel,
    RawEvidence,
    _state_payload,
)
from experiments.world_runtime_writer_canary.semantic_projector_wr2c513r6 import (
    build_delta,
    project,
)


SEMANTIC_EXTRACTOR_VERSION = "world-runtime-semantic-extractor-wr2c513r10-v1"
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


class SemanticExtractionArtifactC513R3(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    extractor_version: Literal[SEMANTIC_EXTRACTOR_VERSION] = SEMANTIC_EXTRACTOR_VERSION
    sample_id: str
    output_hash: str
    raw_event_count: int = Field(ge=0)
    projected_event_count: int = Field(ge=0)
    dropped_events: tuple[DroppedEvent, ...] = ()
    delta: ProposedTypedDeltaV5
    model_calls: Literal[1] = 1
    state_mutated: Literal[False] = False
    commit_sink: Literal["forbidden"] = "forbidden"


SYSTEM_PROMPT = """You judge whether durable world-state changes occur in a
Chinese fiction excerpt. You answer one checklist item per ontology type.
You never output canonical subject/predicate/mechanism/actor/IDs; those are
resolved deterministically after your response. Do not judge legality: an
illegal event that actually occurs in the prose must still be judged as
occurred=true. Never invent an event or evidence quote."""


CHECKLIST = """JUDGE EVERY TYPE BELOW. Output exactly 14 judgments, one per type.
occurred=true only when the prose supports a durable change; otherwise
occurred=false, evidence=[], after_value=null.

1 storefront_public_sale: customer pays (QR code, scan, transfer, cash) and
  goods leave the storefront -> after_value "occurred"
2 storefront_public_handoff: goods are handed out without payment and there is
  no sale in the same scene -> after_value "occurred". A handoff that is part of
  a paid sale (e.g. "付款后递出面包") is NOT a separate handoff.
3 knowledge_state: a character receives and perceives article body (group /
  file / link transmission plus quotation, reply or visible reading) ->
  after_value "perceived". A perception that appears impossible (e.g. the
  draft was never sent, there is no transmission path) is STILL occurred=true;
  legality is decided locally, not by you.
4 resignation_acknowledgement: HR system / HR email / company formally
  acknowledges the resignation -> after_value true
5 unsourced_project_fact: a new persistent role or relationship is introduced
  by narration with a concrete name and title (e.g. "新来的收银员孙岚",
  "new procurement supervisor Han Bing") -> after_value the role word, e.g.
  "收银员". Roles inside a draft/fiction-within-fiction are NOT durable and
  keep occurred=false.
6 object_state: object content / temperature / location changes (poured out,
  washed and stored, moved) -> after_value must be ONE state word: "empty",
  "clean_and_stored", "cold", "hot", or a place word. Do NOT combine states
  into composite values such as "empty_and_restored"; a no-op restore to the
  previous place is not a second change. If the text contains BOTH a pour
  action and a cleaned-and-stored action, produce TWO object_state judgments:
  one "empty" and one "clean_and_stored".
7 repeated_completed_event: an already-completed publish/upload action is
  executed again -> after_value "repeated"
8 employment_state: the employment relationship ends -> after_value "ended".
  This includes the character's own declaration or assumption of termination
  (e.g. "自己认为已结束", "宣布不再来"): still occurred=true even though it
  is illegal without institutional acknowledgement. If the prose also shows
  institutional acknowledgement, judge type 4 too.
9 publication_state: article becomes publicly published after submission ->
  after_value "published"
10 resignation_delivery: resignation reaches the company HR channel ->
   after_value "delivered"
11 resignation_personal_record: resignation is only copied/saved to a private
   mailbox -> after_value "saved"
12 clock_state: prose explicitly advances time (wall clock, minute hand,
   screen clock, or narrative time such as "六点钟", "五点三十一分") ->
   after_value the time, e.g. "06:00" or "六点"
13 location_state: a character explicitly enters a place (workshop, storeroom,
   storefront) -> after_value the place word, e.g. "操作间"
14 storefront_operation_state: the storefront explicitly opens or closes
   (rolling shutter lifted, door opened for business, "关门", "打烊") ->
   after_value "open" or "closed"

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
  {{"change_type":"one of the 14 types","occurred":true|false,
    "after_value":<typed value or null>,"mode":"actual",
    "epistemic":"asserted","evidence":[{{"excerpt":"shortest exact continuous
    quote from FINAL_TEXT","occurrence":1}}]}}
]}}

Rules:
1. All 14 types must appear exactly once.
2. occurred=true only for durable changes in this scene; plans, conditions,
   hearsay, fiction-within-fiction, dreams and negated events keep
   occurred=false. mode=actual, epistemic=asserted only when narration
   directly supports it.
3. Evidence must be exact continuous text copied from FINAL_TEXT. Use 2-4
   excerpts when transmission, perception or prerequisites are separated.
   NEVER join excerpts across a paragraph break: a blank line breaks
   continuity, and a joined excerpt will not be found.  When the needed
   evidence crosses a paragraph break, emit MULTIPLE evidence items for the
   same judgment, one per contiguous span.
4. Do not skip an actual change because it violates CURRENT_STATE or seems
   illegal; the Validator decides legality.
5. Seeing a title/status/screenshot is not body perception. Sending to a
   private mailbox is not institutional delivery. A colleague's premise is not
   employment termination. Looking at a clock is not a clock change unless
   time advances; looking at a door is not entering.
6. after_value must be a single state word; do not merge several changes into
   one composite value.
7. Do not include subject, predicate, mechanism, actor, canonical ID,
   expected_validation, outcome, explanation, or prose outside JSON.
8. Return all 14 judgments even when no durable change occurs.

WORKED EXAMPLE (impossible perception):
FINAL_TEXT: 老吴能逐字背出那篇终稿的结尾，可稿子从未离开过林晚的加密草稿箱，也没有发给过任何人。
Correct judgment for knowledge_state: occurred=true, after_value=perceived,
mode=actual, epistemic=asserted, evidence excerpt=老吴能逐字背出那篇终稿的结尾。
Reason: 能背出=感知已经发生，必须判 occurred=true；是否合法（无传播路径→invalid）
由 Validator 决定。你不得因为“稿子从未发出/不可能”而把它改成 occurred=false。

WORKED EXAMPLE 2 (valid-path knowledge):
FINAL_TEXT: 六点零三分，阿吴在公司群里打开刚上传的文件，念出开头两句。
Correct judgment for knowledge_state: occurred=true, after_value=perceived,
mode=actual, epistemic=asserted, evidence excerpt=阿吴在公司群里打开刚上传的文件，念出开头两句。
Reason: 打开刚上传的文件并念出=真实感知且传播路径存在（群文件+念出→valid）；
不要因为证据摘录较长或事件简短而漏判。

WORKED EXAMPLE 3 (受理确认 is NOT delivery):
FINAL_TEXT: 六点四十四分，人事系统受理了林晚的辞职确认；随后系统把她的用工状态标记为已结束。
Correct judgments: clock_state 06:44 occurred=true; resignation_acknowledgement
occurred=true; employment_state ended occurred=true.
Do NOT add resignation_delivery: 受理确认≠投递/发送；resignation_delivery 只在
辞职信到达公司 HR 渠道（发送/投递）时判 occurred=true。同一证据不得同时计为
acknowledgement 与 delivery。

WORKED EXAMPLE 4 (evidence must be contiguous, never joined across paragraphs):
FINAL_TEXT: 她点下“发布”。（段落空行）页面刷新，文章出现在博客首页。
WRONG: one evidence excerpt joining the two parts across the blank line
  （不会在正文中找到，会导致整条判断被丢弃）。
RIGHT: two evidence items for the same judgment:
  evidence 1 excerpt=她点下“发布”。  evidence 2 excerpt=页面刷新，文章出现在博客首页。
Reason: 每条证据必须是 FINAL_TEXT 的连续子串；跨段落时拆成多条证据，逐条引用。

WORKED EXAMPLE 5 (knowledge evidence must include the transmission segment):
FINAL_TEXT: 六点二十二分，林晚把文章链接发进私聊，季晴看完后回了一条消息。
RIGHT: two evidence items for knowledge_state:
  evidence 1 excerpt=林晚把文章链接发进私聊
  evidence 2 excerpt=季晴看完后回了一条消息
  -> mechanism=private_link_send_and_body_response (valid).
WRONG: only 季晴看完后回了一条消息
  -> transmission segment missing, mechanism becomes missing_transmission_path
  (金标不匹配，判 invalid 方向错误)。
Reason: 知识感知的证据必须同时覆盖传播段与感知段；两段分离时用多条 evidence。

WORKED EXAMPLE 6 (self-assumed employment end must be judged occurred):
FINAL_TEXT: 六点十一分，林晚向吴姐表示，自己干完这周就不来了。
Correct judgment for employment_state: occurred=true, after_value=ended,
evidence excerpt=自己干完这周就不来了。
Reason: 自己宣布/认为结束=声明已发生，必须判 occurred=true（mechanism
self_assumed_effective）；是否合法由 Validator 判 invalid。不得因“未经公司确认/
只是表态”而判 occurred=false。
"""


def build_messages(
    *,
    text: str,
    state_variant: str | None = None,
    state=None,
) -> list[dict[str, str]]:
    if state is None:
        if state_variant is None:
            raise ValueError("wr2c513r10_state_or_state_variant_required")
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
    state_variant: str = "before",
    state=None,
    base_revision: int | None = None,
) -> SemanticExtractionArtifactC513R3:
    """Parse one judgment response, project it, and return the typed delta."""

    try:
        payload = json.loads(response_text)
        raw = RawJudgmentResponse.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("wr2c513r10_invalid_judgment_response") from exc

    if state is None:
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
        raise RuntimeError("wr2c513r10_output_hash_mismatch")
    return SemanticExtractionArtifactC513R3(
        sample_id=sample_id,
        output_hash=output_hash,
        raw_event_count=sum(item["occurred"] for item in judgments),
        projected_event_count=len(events),
        dropped_events=tuple(sorted(dropped, key=lambda item: item.index)),
        delta=delta,
    )
