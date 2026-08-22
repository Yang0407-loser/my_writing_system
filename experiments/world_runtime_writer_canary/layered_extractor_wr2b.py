"""WR2-B layered development extractor.

Pipeline:
1. segment evidence-bearing clauses;
2. detect event candidates without deciding state legality;
3. classify polarity/modality and actor/mechanism;
4. project asserted events into the expanded typed-delta ontology.

The frozen WR2-A adversarial partition is a visible development set here, not
a holdout.  This module has no state commit or production integration.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2a import EvidenceSpan
from experiments.world_runtime_writer_canary.delta_shadow_wr2b import (
    ProposedChangeV2,
    ProposedTypedDeltaV2,
)


EXTRACTOR_VERSION = "world-runtime-layered-extractor-wr2b-v1"
EventType = Literal[
    "payment",
    "public_handoff",
    "body_transmission",
    "body_perception",
    "institutional_acknowledgement",
    "object_change",
    "publication_repeat",
    "employment_end",
    "entity_assertion",
    "publication_transition",
    "resignation_delivery",
    "personal_record",
    "clock_advance",
    "location_entry",
]
Modality = Literal["actual", "planned", "conditional", "hearsay"]
Polarity = Literal["affirmed", "negated"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Clause(FrozenModel):
    clause_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)


class EventCandidate(FrozenModel):
    candidate_id: str
    event_type: EventType
    actor: str
    patient: str
    mechanism: str
    modality: Modality = "actual"
    polarity: Polarity = "affirmed"
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    excerpt: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)


def segment_clauses(text: str) -> tuple[Clause, ...]:
    clauses = []
    for index, match in enumerate(re.finditer(r"[^。！？；\n]+[。！？；]?", text), 1):
        value = match.group(0).strip()
        if not value:
            continue
        leading = len(match.group(0)) - len(match.group(0).lstrip())
        start = match.start() + leading
        clauses.append(Clause(clause_id=f"clause:{index}", start=start, end=start + len(value), text=value))
    return tuple(clauses)


def _span(text: str, pattern: str) -> tuple[int, int, str] | None:
    match = re.search(pattern, text, flags=re.DOTALL)
    return (match.start(), match.end(), match.group(0)) if match else None


def _candidate(
    candidates: list[EventCandidate],
    *,
    event_type: EventType,
    actor: str,
    patient: str,
    mechanism: str,
    span: tuple[int, int, str] | None,
    modality: Modality = "actual",
    polarity: Polarity = "affirmed",
    attributes: dict[str, Any] | None = None,
) -> None:
    if span is None:
        return
    candidates.append(
        EventCandidate(
            candidate_id=f"candidate:{len(candidates) + 1}",
            event_type=event_type,
            actor=actor,
            patient=patient,
            mechanism=mechanism,
            modality=modality,
            polarity=polarity,
            start=span[0],
            end=span[1],
            excerpt=span[2],
            attributes=attributes or {},
        )
    )


def detect_event_candidates(text: str, scene_id: str) -> tuple[EventCandidate, ...]:
    """Detect semantic event frames; this stage does not consult world rules."""

    candidates: list[EventCandidate] = []
    if scene_id == "adversarial-storefront-hours":
        returned = _span(text, r"(?:钞票|纸币|现金|钱)[^。！？；]{0,16}(?:推了回去|退回|推回)")
        _candidate(
            candidates,
            event_type="payment",
            actor="character:lin-wan",
            patient="bakery:wild-bread:storefront",
            mechanism="cash_returned",
            span=returned,
            polarity="negated",
        )
        digital = _span(text, r"手机[^。！？；]{0,12}到账提示")
        cash = _span(text, r"(?:林晚|她|周野)[^。！？；]{0,10}(?:接过|收下)[^。！？；]{0,8}(?:现金|纸币|钱)")
        _candidate(
            candidates,
            event_type="payment",
            actor="character:zhou-ye" if digital else "character:lin-wan",
            patient="bakery:wild-bread:storefront",
            mechanism="digital_payment" if digital else "cash_payment",
            span=digital or cash,
        )
        planned_handoff = _span(text, r"打算[^。！？；]{0,24}(?:卖给|递给|交给)")
        actual_handoff = _span(
            text,
            r"(?:林晚|她|周野)[^。！？；]{0,8}(?:把|将)?[^。！？；]{0,14}(?:面包|欧包|纸袋|早餐)[^。！？；]{0,14}(?:递给|递出|塞进|搁到门外)",
        )
        _candidate(
            candidates,
            event_type="public_handoff",
            actor="character:zhou-ye" if actual_handoff and "周野" in actual_handoff[2] else "character:lin-wan",
            patient="bakery:wild-bread:storefront",
            mechanism="planned_handoff" if planned_handoff else "public_goods_handoff",
            span=planned_handoff or actual_handoff,
            modality="planned" if planned_handoff else "actual",
        )
        clock = _span(text, r"直到[^。！？；]{0,18}(?:五点十分|5[:：]10)")
        _candidate(
            candidates,
            event_type="clock_advance",
            actor="world_clock",
            patient="world_clock",
            mechanism="explicit_time_progression",
            span=clock,
            attributes={"time": "05:10"},
        )
        entry = _span(text, r"周野[^。！？；]{0,10}打开侧门[^。！？；]{0,20}林晚[^。！？；]{0,10}(?:跨过门槛|进入|走进)[^。！？；]{0,12}操作间")
        _candidate(
            candidates,
            event_type="location_entry",
            actor="character:lin-wan",
            patient="bakery:wild-bread:workshop",
            mechanism="explicit_entry",
            span=entry,
        )

    if scene_id in {"adversarial-unpublished-knowledge", "adversarial-object-and-repeat"}:
        planned_send = _span(text, r"(?:答应|打算|以后)[^。！？；]{0,24}(?:发给|发送给)[^。！？；]{0,12}(?:同事|季晴)")
        group_text = _span(text, r"(?:把|将)[^。！？；]{0,18}(?:一段正文|正文|草稿)[^。！？；]{0,12}(?:贴到|发到|粘贴到)(?:公司群|工作群|群里)")
        group_file = _span(text, r"(?:把|将)[^。！？；]{0,12}(?:全文|文件)[^。！？；]{0,12}(?:丢进|发到|发送到)(?:工作群|公司群|群里)")
        private_link = _span(text, r"(?:把|将)[^。！？；]{0,10}(?:链接|全文)[^。！？；]{0,12}(?:私发给|发给)季晴")
        transfer = group_text or group_file or private_link or planned_send
        mechanism = (
            "group_text_send" if group_text else
            "group_file_send" if group_file else
            "private_link_send" if private_link else
            "planned_send"
        )
        recipient = "character:ji-qing" if private_link or (planned_send and "季晴" in planned_send[2]) else "character:coworker"
        _candidate(
            candidates,
            event_type="body_transmission",
            actor="character:lin-wan",
            patient=recipient,
            mechanism=mechanism,
            span=transfer,
            modality="planned" if planned_send and transfer == planned_send else "actual",
        )
        negated_read = _span(text, r"(?:正文[^。！？；]{0,8}(?:没读|没有读)|一个字也没读)")
        hearsay = _span(text, r"转述说[^。！？；]{0,24}(?:好像|听说)[^。！？；]{0,16}(?:看过|读过)")
        response = _span(
            text,
            r"(?:季晴|同事)[^。！？；]{0,18}(?:引用|回复|发来语音|指出)[^。！？；]{0,40}(?:第[一二三四五六七八九十0-9]+段|第[一二三四五六七八九十0-9]+节|结尾|最后一句)",
        )
        simple_response = _span(text, r"同事回复说[^。！？；]{0,24}(?:第[一二三四五六七八九十0-9]+段|结尾)")
        unsupported_claim = _span(text, r"(?:那篇文章|这篇文章)[^。！？；]{0,12}(?:我看了|我读完了|我看完了)")
        perception = response or simple_response or unsupported_claim or negated_read or hearsay
        _candidate(
            candidates,
            event_type="body_perception",
            actor=recipient if transfer else ("character:zhou-ye" if unsupported_claim else "character:coworker"),
            patient="article:lin-wan",
            mechanism="body_specific_response" if response or simple_response else "perception_claim",
            span=perception,
            modality="hearsay" if hearsay else "actual",
            polarity="negated" if negated_read else "affirmed",
        )

    if scene_id == "adversarial-object-and-repeat":
        ack = _span(text, r"系统自动回复[^。！？；]{0,30}(?:辞职信|辞职通知)[^。！？；]{0,20}(?:进入人事流程|已收悉|收到)")
        _candidate(
            candidates,
            event_type="institutional_acknowledgement",
            actor="company:hr-system",
            patient="company:lin-wan",
            mechanism="institutional_reply",
            span=ack,
        )
        pour = _span(text, r"林晚[^。！？；]{0,12}(?:把)?绿豆汤倒进水槽[^。！？；]{0,12}(?:空碗|碗)")
        unexplained = _span(text, r"门锁[^。！？；]{0,16}没有动过[。！？；]?[^。！？；]{0,40}碗已经洗净并收进橱柜")
        warmed = _span(text, r"阳光[^。！？；]{0,16}(?:碗里的汤|绿豆汤)[^。！？；]{0,16}(?:冰凉变成温热|变温|温热)")
        object_span = pour or unexplained or warmed
        mechanism = "actor_pours_out" if pour else "missing_actor_or_event" if unexplained else "sunlight_warming"
        after_value = "empty" if pour else "clean_and_stored" if unexplained else "warm"
        predicate = "temperature_state" if warmed else "content_state"
        _candidate(
            candidates,
            event_type="object_change",
            actor="character:lin-wan" if pour else "sunlight" if warmed else "unknown",
            patient="object:green-bean-soup-bowl",
            mechanism=mechanism,
            span=object_span,
            attributes={"predicate": predicate, "after_value": after_value},
        )
        repeat = _span(text, r"(?:仍)?又按了一遍发布键|重新发布文章|再次发布文章")
        _candidate(
            candidates,
            event_type="publication_repeat",
            actor="character:lin-wan",
            patient="article:lin-wan",
            mechanism="explicit_repeat_marker",
            span=repeat,
        )
        publication = _span(text, r"(?:提交终稿|提交文章)[^。！？；]{0,24}(?:审核通过|发布成功|变成已发布)")
        _candidate(
            candidates,
            event_type="publication_transition",
            actor="character:lin-wan",
            patient="article:lin-wan",
            mechanism="submit_and_platform_publish",
            span=publication,
        )

    if scene_id == "adversarial-unpublished-knowledge":
        publication = _span(text, r"(?:提交终稿|提交文章)[^。！？；]{0,24}(?:审核通过|发布成功|变成已发布)")
        _candidate(
            candidates,
            event_type="publication_transition",
            actor="character:lin-wan",
            patient="article:lin-wan",
            mechanism="submit_and_platform_publish",
            span=publication,
        )

    if scene_id == "adversarial-employment-transition":
        conditional = _span(text, r"如果[^。！？；]{0,24}(?:不再上班|离职|辞职)")
        ack = _span(text, r"人事[^。！？；]{0,14}(?:正式确认|确认)[^。！？；]{0,18}(?:辞职[^。！？；]{0,8}生效|辞职生效)")
        _candidate(
            candidates,
            event_type="institutional_acknowledgement",
            actor="company:hr-system",
            patient="company:lin-wan",
            mechanism="institutional_reply",
            span=ack,
        )
        valid_end = _span(text, r"(?:劳动关系|雇佣关系)[^。！？；]{0,12}(?:结束|终止)")
        assumed_end = _span(text, r"人事[^。！？；]{0,10}(?:没回信|没有回复)[^。！？；]{0,30}(?:不再是公司员工|辞职已经生效|辞职已生效)")
        end_span = valid_end or assumed_end or conditional
        _candidate(
            candidates,
            event_type="employment_end",
            actor="company:hr-system" if valid_end else "character:lin-wan",
            patient="employment:lin-wan",
            mechanism="acknowledged_effective_resignation" if valid_end and ack else "self_assumed_effective",
            span=end_span,
            modality="conditional" if conditional and end_span == conditional else "actual",
        )
        delivery = _span(text, r"(?:把|将)辞职通知发送到公司人事邮箱[^。！？；]{0,16}(?:投递[^。！？；]{0,6}成功|发送成功)")
        _candidate(
            candidates,
            event_type="resignation_delivery",
            actor="character:lin-wan",
            patient="resignation:lin-wan",
            mechanism="institutional_email_delivery",
            span=delivery,
        )
        personal = _span(text, r"(?:把|将)辞职信转发到自己的私人邮箱留档")
        _candidate(
            candidates,
            event_type="personal_record",
            actor="character:lin-wan",
            patient="resignation:lin-wan",
            mechanism="private_email_copy",
            span=personal,
        )

    if "小说里的" not in text and "正文里的" not in text:
        entity = _span(text, r"负责排班的(?P<name>赵敏)")
        _candidate(
            candidates,
            event_type="entity_assertion",
            actor="narrator",
            patient="character:zhao-min",
            mechanism="text_assertion",
            span=entity,
            attributes={"predicate": "identity_role", "after_value": "bakery_scheduler"},
        )

    return tuple(candidates)


class _Builder:
    def __init__(self, *, text: str, sample_id: str, scene_id: str, state_variant: str, base_revision: int):
        self.text = text
        self.sample_id = sample_id
        self.scene_id = scene_id
        self.state_variant = state_variant
        self.base_revision = base_revision
        self.evidence: list[EvidenceSpan] = []
        self.changes: list[ProposedChangeV2] = []
        self._evidence_by_candidate: dict[str, str] = {}

    def evidence_id(self, candidate: EventCandidate) -> str:
        if candidate.candidate_id in self._evidence_by_candidate:
            return self._evidence_by_candidate[candidate.candidate_id]
        evidence_id = f"ev:wr2b:{self.sample_id.lower()}:{len(self.evidence) + 1}"
        self.evidence.append(
            EvidenceSpan(
                evidence_id=evidence_id,
                claim=f"{candidate.event_type} via {candidate.mechanism}",
                start=candidate.start,
                end=candidate.end,
                excerpt=candidate.excerpt,
            )
        )
        self._evidence_by_candidate[candidate.candidate_id] = evidence_id
        return evidence_id

    def change(self, label: str, candidates: tuple[EventCandidate, ...], **values: Any) -> None:
        self.changes.append(
            ProposedChangeV2(
                change_id=f"change:wr2b:{self.sample_id.lower()}:{label}",
                sequence=len(self.changes) + 1,
                evidence_ids=tuple(self.evidence_id(item) for item in candidates),
                **values,
            )
        )

    def build(self) -> ProposedTypedDeltaV2:
        return ProposedTypedDeltaV2(
            delta_id=f"delta:wr2b:{self.sample_id.lower()}",
            sample_id=self.sample_id,
            scene_id=self.scene_id,
            project_id="project:saturday-bakery",
            state_variant=self.state_variant,
            base_revision=self.base_revision,
            output_hash=hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
            evidence=tuple(self.evidence),
            changes=tuple(self.changes),
        )


def project_candidates(
    *,
    text: str,
    sample_id: str,
    scene_id: str,
    state_variant: str,
    base_revision: int,
    candidates: tuple[EventCandidate, ...],
) -> ProposedTypedDeltaV2:
    """Project asserted event frames into typed state-change proposals."""

    builder = _Builder(text=text, sample_id=sample_id, scene_id=scene_id, state_variant=state_variant, base_revision=base_revision)
    actual = [item for item in candidates if item.modality == "actual" and item.polarity == "affirmed"]

    payments = [item for item in actual if item.event_type == "payment"]
    handoffs = [item for item in actual if item.event_type == "public_handoff"]
    if handoffs and payments:
        payment, handoff = payments[0], handoffs[0]
        mechanism = "digital_payment_exchange" if payment.mechanism == "digital_payment" else "cash_exchange"
        builder.change(
            "public-sale", (payment, handoff), change_type="storefront_public_sale",
            subject="bakery:wild-bread:storefront", predicate="public_sale_event",
            before_value=None, before_epistemic_status="unknown", after_value="occurred",
            actor=handoff.actor, mechanism=mechanism, event_id="event:visitor-purchase",
        )
    elif handoffs:
        builder.change(
            "public-handoff", (handoffs[0],), change_type="storefront_public_handoff",
            subject="bakery:wild-bread:storefront", predicate="public_goods_handoff",
            before_value=None, before_epistemic_status="unknown", after_value="occurred",
            actor=handoffs[0].actor, mechanism="free_handoff", event_id="event:visitor-free-handoff",
        )

    transfers = [item for item in actual if item.event_type == "body_transmission"]
    perceptions = [item for item in actual if item.event_type == "body_perception"]
    if perceptions:
        perception = perceptions[0]
        matching = next((item for item in transfers if item.patient == perception.actor), None)
        if matching:
            mechanism = {
                "group_text_send": "explicit_group_send_and_body_response",
                "group_file_send": "group_file_send_and_body_response",
                "private_link_send": "private_link_send_and_body_response",
            }[matching.mechanism]
            evidence = (matching, perception)
            subject = matching.patient
        else:
            mechanism = "missing_transmission_path"
            evidence = (perception,)
            subject = perception.actor
        builder.change(
            "knowledge", evidence, change_type="knowledge_state",
            subject=subject, predicate="article_knowledge", before_value=None,
            before_epistemic_status="unknown", after_value="perceived", actor=subject,
            mechanism=mechanism, event_id="event:article-perception",
        )

    for candidate in actual:
        if candidate.event_type == "institutional_acknowledgement":
            builder.change(
                "resignation-ack", (candidate,), change_type="resignation_acknowledgement",
                subject="company:lin-wan", predicate="resignation_acknowledged", before_value=None,
                before_epistemic_status="unknown", after_value=True, actor="company:hr-system",
                mechanism="institutional_reply", event_id="event:resignation-acknowledged",
            )
        elif candidate.event_type == "object_change":
            predicate = candidate.attributes["predicate"]
            before = "contains_cold_soup" if predicate == "content_state" else "cold"
            builder.change(
                f"object-{len(builder.changes) + 1}", (candidate,), change_type="object_state",
                subject=candidate.patient, predicate=predicate, before_value=before,
                before_epistemic_status="confirmed_true", after_value=candidate.attributes["after_value"],
                actor=candidate.actor, mechanism=candidate.mechanism, event_id="event:bowl-state-change",
            )
        elif candidate.event_type == "publication_repeat":
            builder.change(
                "publication-repeat", (candidate,), change_type="repeated_completed_event",
                subject="article:lin-wan", predicate="publication_event", before_value="completed",
                before_epistemic_status="confirmed_true", after_value="repeated", actor=candidate.actor,
                mechanism="explicit_repeat_marker", event_id="event:article-published",
            )
        elif candidate.event_type == "employment_end":
            builder.change(
                "employment-ended", (candidate,), change_type="employment_state",
                subject="employment:lin-wan", predicate="status", before_value="employed",
                before_epistemic_status="confirmed_true", after_value="ended", actor=candidate.actor,
                mechanism=candidate.mechanism, event_id="event:employment-ended",
            )
        elif candidate.event_type == "entity_assertion":
            builder.change(
                "unsourced-entity", (candidate,), change_type="unsourced_project_fact",
                subject=candidate.patient, predicate=candidate.attributes["predicate"], before_value=None,
                before_epistemic_status="unknown", after_value=candidate.attributes["after_value"], actor="narrator",
                mechanism="text_assertion", event_id=None,
            )
        elif candidate.event_type == "publication_transition":
            builder.change(
                "publication", (candidate,), change_type="publication_state",
                subject="article:lin-wan", predicate="publication_state", before_value="draft",
                before_epistemic_status="confirmed_true", after_value="published", actor="character:lin-wan",
                mechanism="submit_and_platform_publish", event_id="event:article-published",
            )
        elif candidate.event_type == "resignation_delivery":
            builder.change(
                "resignation-delivery", (candidate,), change_type="resignation_delivery",
                subject="resignation:lin-wan", predicate="lifecycle_state", before_value="private_draft",
                before_epistemic_status="confirmed_true", after_value="delivered", actor="character:lin-wan",
                mechanism="institutional_email_delivery", event_id="event:resignation-delivered",
            )
        elif candidate.event_type == "personal_record":
            builder.change(
                "personal-record", (candidate,), change_type="resignation_personal_record",
                subject="resignation:lin-wan", predicate="personal_record_state", before_value=None,
                before_epistemic_status="unknown", after_value="saved", actor="character:lin-wan",
                mechanism="private_email_copy", event_id="event:resignation-personal-copy",
            )
        elif candidate.event_type == "clock_advance":
            builder.change(
                "clock", (candidate,), change_type="clock_state", subject="world_clock", predicate="time",
                before_value="04:20", before_epistemic_status="confirmed_true", after_value=candidate.attributes["time"],
                actor="world_clock", mechanism="explicit_time_progression", event_id="event:clock-advance",
            )
        elif candidate.event_type == "location_entry":
            builder.change(
                "location", (candidate,), change_type="location_state", subject="character:lin-wan",
                predicate="location", before_value=None, before_epistemic_status="unknown",
                after_value="bakery:wild-bread:workshop", actor="character:lin-wan",
                mechanism="explicit_entry", event_id="event:lin-wan-enters-workshop",
            )
    return builder.build()


def extract_typed_delta_v2(
    *, text: str, sample_id: str, scene_id: str, state_variant: str, base_revision: int | None = None
) -> tuple[ProposedTypedDeltaV2, tuple[Clause, ...], tuple[EventCandidate, ...]]:
    _, states, _ = wr1r._artifacts()
    revision = states[state_variant].revision if base_revision is None else base_revision
    clauses = segment_clauses(text)
    candidates = detect_event_candidates(text, scene_id)
    delta = project_candidates(
        text=text,
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
        base_revision=revision,
        candidates=candidates,
    )
    return delta, clauses, candidates

