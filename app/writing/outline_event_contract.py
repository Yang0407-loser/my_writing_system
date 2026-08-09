"""Typed, traceable event facts derived from an outline.

Legacy adaptation is deliberately conservative: it proposes structure for an
author to review, but never upgrades inferred events into hard requirements.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict


OUTLINE_EVENT_CONTRACT_VERSION = "outline-event-contract-v1"

UnitType = Literal[
    "action_chain",
    "dialogue_interaction",
    "decision",
    "state_transition",
    "observation",
    "scene_transition",
    "unresolved",
]
TemporalScope = Literal["current", "past", "future", "mixed", "unknown"]
Requiredness = Literal["hard", "soft", "unspecified"]
EventStatus = Literal["proposed", "confirmed", "stale", "superseded"]
Confidence = Literal["high", "medium", "low"]

_SPACE_RE = re.compile(r"\s+")
_MAJOR_SPLIT_RE = re.compile(r"[。！？；\n]+")
_CLAUSE_SPLIT_RE = re.compile(r"[，,]+")
_TIME_PATTERNS = (
    re.compile(r"第[一二三四五六七八九十\d]+个?(?:周[一二三四五六日天]|星期[一二三四五六日天])"),
    re.compile(r"(?:周|星期)[一二三四五六日天]"),
    re.compile(r"(?:凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|深夜)\s*\d{0,2}点?"),
    re.compile(r"(?:当天|当晚|次日|翌日|第二天|隔天|数日后|几天后|下周|随后|之后|后来)"),
)
_LOCATION_RE = re.compile(
    r"(?:在|到|回到|走进|离开|站在)([\u4e00-\u9fff]{0,8}"
    r"(?:面包店|书店|医院|公司|家中|家里|门口|社区|街道|车站|厨房|办公室|店里|店外))"
)
_INTERACTION_MARKERS = (
    "邀请", "回答", "回应", "发问", "询问", "递给", "交谈", "交流", "相遇",
    "偶遇", "对话", "告诉", "拒绝", "答应", "承诺", "道谢", "叮嘱",
    "递水", "递出", "递来", "接过",
)
_DECISION_MARKERS = ("决定", "选择", "打算", "计划", "放弃", "承诺", "答应", "拒绝")
_STATE_MARKERS = (
    "辞职", "离职", "确立", "完成", "死亡", "住院", "负债", "分手",
    "结婚", "搬家", "失去", "获得", "成为",
)
_OBSERVATION_MARKERS = (
    "观察", "看见", "看到", "听见", "听到", "闻到", "闻见", "感受", "想到", "想起", "反思", "意识到",
)
_ACTION_MARKERS = _INTERACTION_MARKERS + _DECISION_MARKERS + _STATE_MARKERS + (
    "收到", "写下", "拍下", "拍到", "按下", "走出", "回到", "收拾", "记录",
    "打开", "关闭", "开始", "结束", "发现", "遇见", "站到", "退出", "递来",
)
_PAST_MARKERS = ("曾经", "此前", "之前", "上周", "五天前", "当年", "回忆", "想起")
_FUTURE_MARKERS = ("明天", "次日", "下周", "以后", "未来", "准备", "打算", "计划", "将要")


def _canonical_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _normalise(text: str) -> str:
    compact = _SPACE_RE.sub("", str(text or ""))
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", compact).lower()


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _time_anchors(text: str) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    for pattern in _TIME_PATTERNS:
        matches.extend(
            (match.start(), match.end(), match.group(0))
            for match in pattern.finditer(text)
        )
    selected: list[tuple[int, int, str]] = []
    for start, end, value in sorted(
        matches, key=lambda item: (-(item[1] - item[0]), item[0])
    ):
        if any(start < kept_end and end > kept_start for kept_start, kept_end, _ in selected):
            continue
        selected.append((start, end, value))
    return _unique(value for _, _, value in sorted(selected))


def _location(text: str) -> str | None:
    match = _LOCATION_RE.search(text)
    return match.group(1) if match else None


def _contains_action(text: str) -> bool:
    return any(marker in text for marker in _ACTION_MARKERS)


def _temporal_scope(text: str) -> TemporalScope:
    has_past = any(marker in text for marker in _PAST_MARKERS)
    has_future = any(marker in text for marker in _FUTURE_MARKERS)
    if has_past and has_future:
        return "mixed"
    if has_past:
        return "past"
    if has_future:
        return "future"
    return "current" if _contains_action(text) else "unknown"


def _unit_type(text: str, actors: list[str], location: str | None) -> UnitType:
    if len(actors) >= 2 and any(marker in text for marker in _INTERACTION_MARKERS):
        return "dialogue_interaction"
    if any(marker in text for marker in _DECISION_MARKERS):
        return "decision"
    if any(marker in text for marker in _STATE_MARKERS):
        return "state_transition"
    if location and any(marker in text for marker in ("走进", "离开", "回到", "退出", "来到")):
        return "scene_transition"
    if any(marker in text for marker in _OBSERVATION_MARKERS):
        return "observation"
    if _contains_action(text):
        return "action_chain"
    return "unresolved"


def _subsection_source_hash(
    *, title: str, description: str, key_points: list[str]
) -> str:
    return _canonical_hash({
        "title": str(title or ""),
        "description": str(description or ""),
        "key_points": [str(item) for item in key_points],
    })


class FrozenContractModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ContractSourceRef(FrozenContractModel):
    source_id: str
    source_hash: str


class OutlineEventUnit(FrozenContractModel):
    event_id: str
    section: int
    subsection: int
    source_slot: str
    source_id: str
    source_hash: str
    text_hash: str
    unit_type: UnitType
    summary: str
    actors: tuple[str, ...] = ()
    action: str = ""
    object: str = ""
    result: str = ""
    time_anchor: str | None = None
    location_anchor: str | None = None
    temporal_scope: TemporalScope
    requiredness: Requiredness = "unspecified"
    status: EventStatus = "proposed"
    confidence: Confidence
    extraction_reason: str
    user_confirmed: bool = False
    invalidation_reason: str | None = None


class SubsectionEventContract(FrozenContractModel):
    contract_id: str
    section: int
    subsection: int
    title: str
    objective: str
    current_target_words: int
    events: tuple[OutlineEventUnit, ...]
    required_event_ids: tuple[str, ...] = ()
    optional_event_ids: tuple[str, ...] = ()
    deferred_event_ids: tuple[str, ...] = ()
    stop_after_event_id: str | None = None
    source_manifest: tuple[ContractSourceRef, ...] = ()
    source_hash: str
    boundary_source_hash: str | None = None
    contract_hash: str
    schema_version: str = OUTLINE_EVENT_CONTRACT_VERSION
    confidence: Confidence
    status: EventStatus = "proposed"


class ChapterEventContract(FrozenContractModel):
    section: int
    subsection_contracts: tuple[SubsectionEventContract, ...]
    chapter_target_words: int
    contract_hash: str
    source_manifest: tuple[ContractSourceRef, ...] = ()
    stale_subsections: tuple[int, ...] = ()
    schema_version: str = OUTLINE_EVENT_CONTRACT_VERSION


class LegacyOutlineEventAdapter:
    """Propose event units without claiming machine-inferred authority."""

    def extract(
        self,
        *,
        section: int,
        subsection: int,
        source_id: str,
        description: str,
        key_points: list[str],
        character_names: list[str],
    ) -> tuple[OutlineEventUnit, ...]:
        points = [str(point).strip() for point in key_points if str(point).strip()]
        candidates: list[tuple[str, str, str, Confidence, str]] = []
        seen: set[str] = set()

        def add(text: str, slot: str, sid: str, confidence: Confidence, reason: str) -> None:
            cleaned = str(text).strip(" ，,。；;\n\t")
            normalised = _normalise(cleaned)
            if not normalised or normalised in seen:
                return
            if any(normalised in prior or prior in normalised for prior in seen):
                return
            seen.add(normalised)
            candidates.append((cleaned, slot, sid, confidence, reason))

        if points:
            for index, point in enumerate(points, 1):
                actors = [name for name in character_names if name and name in point]
                broad = len(_normalise(point)) < 12 or (
                    not actors and not _contains_action(point)
                )
                add(
                    point,
                    f"kp:{index:03d}",
                    f"{source_id}:key_point:{index}",
                    "low" if broad else "medium",
                    "legacy_explicit_key_point",
                )
            point_text = "".join(points)
            point_markers = {
                marker for marker in _ACTION_MARKERS if marker in point_text
            }
            point_anchors = set(_time_anchors(point_text))
            for index, beat in enumerate(_MAJOR_SPLIT_RE.split(description), 1):
                beat = beat.strip()
                if not _contains_action(beat):
                    continue
                beat_markers = {marker for marker in _ACTION_MARKERS if marker in beat}
                beat_anchors = set(_time_anchors(beat))
                # A multi-action description sentence is normally a subsection
                # synopsis, not one independently traceable supplemental event.
                # Keep it in the contract objective, but do not double-count it.
                action_clauses = [
                    clause
                    for clause in _CLAUSE_SPLIT_RE.split(beat)
                    if _contains_action(clause)
                ]
                if len(action_clauses) > 1:
                    continue
                if beat_markers <= point_markers and beat_anchors <= point_anchors:
                    continue
                add(
                    beat,
                    f"desc:{index:03d}",
                    f"{source_id}:description:{index}",
                    "low",
                    "legacy_description_supplement",
                )
        else:
            beats = self._description_beats(description)
            for index, beat in enumerate(beats, 1):
                add(
                    beat,
                    f"desc:{index:03d}",
                    f"{source_id}:description:{index}",
                    "low",
                    "legacy_description_fallback",
                )

        units: list[OutlineEventUnit] = []
        for text, slot, sid, confidence, reason in candidates:
            actors = [name for name in character_names if name and name in text]
            anchors = _time_anchors(text)
            location = _location(text)
            event_id = f"outline:S{section}.{subsection}:{slot}"
            units.append(OutlineEventUnit(
                event_id=event_id,
                section=section,
                subsection=subsection,
                source_slot=slot,
                source_id=sid,
                source_hash=_canonical_hash({"source_id": sid, "text": text}),
                text_hash=_text_hash(text),
                unit_type=_unit_type(text, actors, location),
                summary=text,
                actors=tuple(actors),
                action=text,
                time_anchor="｜".join(anchors) if anchors else None,
                location_anchor=location,
                temporal_scope=_temporal_scope(text),
                requiredness="unspecified",
                status="proposed",
                confidence=confidence,
                extraction_reason=reason,
                user_confirmed=False,
            ))
        return tuple(units)

    @staticmethod
    def _description_beats(description: str) -> list[str]:
        result: list[str] = []
        for segment in _MAJOR_SPLIT_RE.split(str(description or "")):
            segment = segment.strip()
            if not segment:
                continue
            if len(_normalise(segment)) > 35 and not (
                sum(marker in segment for marker in _INTERACTION_MARKERS) >= 2
            ):
                clauses = [
                    clause.strip()
                    for clause in _CLAUSE_SPLIT_RE.split(segment)
                    if _contains_action(clause)
                ]
                if len(clauses) >= 2:
                    result.extend(clauses)
                    continue
            if _contains_action(segment):
                result.append(segment)
        return result


class OutlineEventContractCompiler:
    """Compile proposed or author-confirmed contracts without side effects."""

    def __init__(self, adapter: LegacyOutlineEventAdapter | None = None) -> None:
        self.adapter = adapter or LegacyOutlineEventAdapter()

    def compile_chapter(
        self,
        *,
        section: int,
        subsections: list[Mapping[str, object]],
        character_names: list[str],
        chapter_target_words: int,
    ) -> ChapterEventContract:
        proposed = [
            self._proposed_events(
                section=section,
                subsection=int(sub.get("subsection") or index),
                sub=sub,
                character_names=character_names,
            )
            for index, sub in enumerate(subsections, 1)
        ]
        contracts: list[SubsectionEventContract] = []
        for index, sub in enumerate(subsections):
            current_events = proposed[index]
            next_events = proposed[index + 1] if index + 1 < len(proposed) else ()
            contracts.append(self._compile_subsection(
                section=section,
                subsection=int(sub.get("subsection") or index + 1),
                sub=sub,
                events=current_events,
                next_events=next_events,
            ))
        manifest = self._manifest(
            ref
            for contract in contracts
            for ref in contract.source_manifest
        )
        stale = tuple(
            contract.subsection
            for contract in contracts
            if contract.status == "stale"
        )
        payload = {
            "section": section,
            "chapter_target_words": int(chapter_target_words),
            "subsection_contract_hashes": [
                contract.contract_hash for contract in contracts
            ],
            "stale_subsections": stale,
        }
        return ChapterEventContract(
            section=section,
            subsection_contracts=tuple(contracts),
            chapter_target_words=int(chapter_target_words),
            contract_hash=_canonical_hash(payload),
            source_manifest=manifest,
            stale_subsections=stale,
        )

    def confirm_submission(
        self,
        *,
        section: int,
        subsection: int,
        sub: Mapping[str, object],
        submitted: Mapping[str, object],
        next_sub: Mapping[str, object] | None = None,
    ) -> SubsectionEventContract:
        actor_names = _unique(
            actor
            for event in submitted.get("events", [])
            if isinstance(event, Mapping)
            for actor in event.get("actors", [])
            if str(actor).strip()
        )
        proposed = self._proposed_events(
            section=section,
            subsection=subsection,
            sub=sub,
            character_names=actor_names,
        )
        next_events = (
            self._proposed_events(
                section=section,
                subsection=subsection + 1,
                sub=next_sub,
                character_names=actor_names,
            )
            if next_sub is not None
            else ()
        )
        base = self._build_contract(
            section=section,
            subsection=subsection,
            sub=sub,
            events=proposed,
            next_events=next_events,
            status="proposed",
        )
        submitted_by_id = {
            str(event.get("event_id")): event
            for event in submitted.get("events", [])
            if isinstance(event, Mapping)
        }
        submitted_by_text_hash = {
            str(event.get("text_hash")): event
            for event in submitted.get("events", [])
            if isinstance(event, Mapping) and event.get("text_hash")
        }
        confirmed: list[OutlineEventUnit] = []
        used_event_ids: set[str] = set()
        for event in base.events:
            choice = submitted_by_id.get(event.event_id, {})
            if not choice or str(choice.get("text_hash", "")) != event.text_hash:
                choice = submitted_by_text_hash.get(event.text_hash, {})
            if choice and str(choice.get("text_hash", "")) == event.text_hash:
                requiredness = str(choice.get("requiredness", "unspecified"))
                temporal_scope = str(choice.get("temporal_scope", event.temporal_scope))
                submitted_actors = tuple(_unique(
                    str(actor).strip()
                    for actor in choice.get("actors", [])
                    if str(actor).strip()
                ))
                chosen_id = str(choice.get("event_id") or event.event_id)
                if chosen_id in used_event_ids:
                    chosen_id = (
                        f"outline:S{section}.{subsection}:"
                        f"{event.source_slot}:new:{event.text_hash[:10]}"
                    )
                if requiredness not in {"hard", "soft", "unspecified"}:
                    requiredness = "unspecified"
                if temporal_scope not in {"current", "past", "future", "mixed", "unknown"}:
                    temporal_scope = event.temporal_scope
                confirmed.append(event.model_copy(update={
                    "event_id": chosen_id,
                    "requiredness": requiredness,
                    "temporal_scope": temporal_scope,
                    "actors": submitted_actors or event.actors,
                    "status": "confirmed",
                    "user_confirmed": True,
                    "confidence": "high",
                    "invalidation_reason": None,
                }))
                used_event_ids.add(chosen_id)
            else:
                confirmed.append(event)
                used_event_ids.add(event.event_id)
        valid_ids = {event.event_id for event in confirmed if event.status != "superseded"}
        requested_stop = submitted.get("stop_after_event_id")
        stop_after = str(requested_stop) if requested_stop in valid_ids else None
        return self._build_contract(
            section=section,
            subsection=subsection,
            sub=sub,
            events=tuple(confirmed),
            next_events=next_events,
            status="confirmed",
            stop_after_event_id=stop_after,
        )

    def _proposed_events(
        self,
        *,
        section: int,
        subsection: int,
        sub: Mapping[str, object],
        character_names: list[str],
    ) -> tuple[OutlineEventUnit, ...]:
        source_id = str(
            sub.get("source_id")
            or sub.get("id")
            or f"outline:S{section}.{subsection}"
        )
        return self.adapter.extract(
            section=section,
            subsection=subsection,
            source_id=source_id,
            description=str(sub.get("description") or ""),
            key_points=list(sub.get("key_points") or []),
            character_names=character_names,
        )

    def _compile_subsection(
        self,
        *,
        section: int,
        subsection: int,
        sub: Mapping[str, object],
        events: tuple[OutlineEventUnit, ...],
        next_events: tuple[OutlineEventUnit, ...],
    ) -> SubsectionEventContract:
        proposed = self._build_contract(
            section=section,
            subsection=subsection,
            sub=sub,
            events=events,
            next_events=next_events,
            status="proposed",
        )
        stored = sub.get("event_contract")
        if not isinstance(stored, Mapping):
            return proposed
        try:
            prior = SubsectionEventContract.model_validate(stored)
        except Exception:
            return proposed
        if prior.status == "confirmed" and prior.source_hash == proposed.source_hash:
            return self._build_contract(
                section=section,
                subsection=subsection,
                sub=sub,
                events=prior.events,
                next_events=next_events,
                status="confirmed",
                stop_after_event_id=prior.stop_after_event_id,
            )
        if prior.status != "confirmed":
            return proposed
        unmatched = list(proposed.events)
        stale: list[OutlineEventUnit] = []
        reserved_ids = {event.event_id for event in prior.events}
        exact_matches: dict[str, OutlineEventUnit] = {}
        for old in prior.events:
            replacement = next(
                (event for event in unmatched if event.text_hash == old.text_hash),
                None,
            )
            if replacement is not None:
                exact_matches[old.event_id] = replacement
                unmatched.remove(replacement)
        for old in prior.events:
            replacement = exact_matches.get(old.event_id)
            if replacement is not None:
                stale.append(replacement.model_copy(update={
                    "event_id": old.event_id,
                    "requiredness": old.requiredness,
                    "temporal_scope": old.temporal_scope,
                    "status": "confirmed",
                    "user_confirmed": True,
                    "confidence": "high",
                    "invalidation_reason": None,
                }))
                continue
            replacement = next(
                (event for event in unmatched if event.source_slot == old.source_slot),
                None,
            )
            if replacement is None:
                stale.append(old.model_copy(update={
                    "status": "superseded",
                    "user_confirmed": False,
                    "requiredness": "unspecified",
                    "invalidation_reason": "source_removed",
                }))
                continue
            unmatched.remove(replacement)
            stale.append(replacement.model_copy(update={
                "event_id": old.event_id,
                "status": "stale",
                "user_confirmed": False,
                "requiredness": "unspecified",
                "invalidation_reason": "source_hash_changed",
            }))
        for event in unmatched:
            event_id = event.event_id
            if event_id in reserved_ids:
                event_id = (
                    f"outline:S{section}.{subsection}:"
                    f"{event.source_slot}:new:{event.text_hash[:10]}"
                )
            stale.append(event.model_copy(update={"event_id": event_id}))
        active_ids = {
            event.event_id for event in stale if event.status != "superseded"
        }
        stop_after = (
            prior.stop_after_event_id
            if prior.stop_after_event_id in active_ids
            else None
        )
        return self._build_contract(
            section=section,
            subsection=subsection,
            sub=sub,
            events=tuple(stale),
            next_events=next_events,
            status="stale",
            stop_after_event_id=stop_after,
        )

    def _build_contract(
        self,
        *,
        section: int,
        subsection: int,
        sub: Mapping[str, object],
        events: tuple[OutlineEventUnit, ...],
        next_events: tuple[OutlineEventUnit, ...],
        status: EventStatus,
        stop_after_event_id: str | None = None,
    ) -> SubsectionEventContract:
        title = str(sub.get("title") or "")
        description = str(sub.get("description") or "")
        key_points = list(sub.get("key_points") or [])
        source_hash = _subsection_source_hash(
            title=title, description=description, key_points=key_points
        )
        boundary_source_hash = (
            _canonical_hash([event.source_hash for event in next_events])
            if next_events else None
        )
        active = tuple(event for event in events if event.status != "superseded")
        required = tuple(
            event.event_id for event in active if event.requiredness == "hard"
        )
        optional = tuple(
            event.event_id for event in active if event.requiredness != "hard"
        )
        deferred = tuple(event.event_id for event in next_events)
        manifest = self._manifest(
            [
                ContractSourceRef(
                    source_id=event.source_id, source_hash=event.source_hash
                )
                for event in events + next_events
            ]
        )
        confidence: Confidence
        if not active or any(event.confidence == "low" for event in active):
            confidence = "low"
        elif all(event.confidence == "high" for event in active):
            confidence = "high"
        else:
            confidence = "medium"
        payload = {
            "section": section,
            "subsection": subsection,
            "title": title,
            "objective": description or title,
            "target_words": int(sub.get("target_words") or 0),
            "events": [event.model_dump(mode="json") for event in events],
            "required_event_ids": required,
            "optional_event_ids": optional,
            "deferred_event_ids": deferred,
            "stop_after_event_id": stop_after_event_id,
            "source_hash": source_hash,
            "boundary_source_hash": boundary_source_hash,
            "status": status,
            "schema_version": OUTLINE_EVENT_CONTRACT_VERSION,
        }
        return SubsectionEventContract(
            contract_id=f"outline:S{section}.{subsection}:contract",
            section=section,
            subsection=subsection,
            title=title,
            objective=description or title,
            current_target_words=int(sub.get("target_words") or 0),
            events=events,
            required_event_ids=required,
            optional_event_ids=optional,
            deferred_event_ids=deferred,
            stop_after_event_id=stop_after_event_id,
            source_manifest=manifest,
            source_hash=source_hash,
            boundary_source_hash=boundary_source_hash,
            contract_hash=_canonical_hash(payload),
            confidence=confidence,
            status=status,
        )

    @staticmethod
    def _manifest(
        refs: Iterable[ContractSourceRef],
    ) -> tuple[ContractSourceRef, ...]:
        unique: dict[tuple[str, str], ContractSourceRef] = {}
        for ref in refs:
            unique[(ref.source_id, ref.source_hash)] = ref
        return tuple(unique.values())


def canonicalise_confirmed_tree(tree: list[dict]) -> list[dict]:
    """Validate requested confirmations in-place-compatible copied tree data."""
    compiler = OutlineEventContractCompiler()
    for section_index, section in enumerate(tree, 1):
        children = section.get("children", [])
        actor_names = _unique(
            str(actor)
            for sub in children
            for contract in [sub.get("event_contract")]
            if isinstance(contract, Mapping)
            for event in contract.get("events", [])
            if isinstance(event, Mapping)
            for actor in event.get("actors", [])
            if str(actor).strip()
        )
        compiled = compiler.compile_chapter(
            section=section_index,
            subsections=[
                dict(sub, subsection=index, source_id=sub.get("id"))
                for index, sub in enumerate(children, 1)
            ],
            character_names=actor_names,
            chapter_target_words=sum(
                int(sub.get("target_words") or 0) for sub in children
            ),
        )
        for subsection_index, sub in enumerate(children, 1):
            submitted = sub.get("event_contract")
            if not isinstance(submitted, Mapping):
                continue
            if submitted.get("status") != "confirmed":
                continue
            if submitted.get("confirmation_requested") is True:
                contract = compiler.confirm_submission(
                    section=section_index,
                    subsection=subsection_index,
                    sub=sub,
                    submitted=submitted,
                    next_sub=(
                        children[subsection_index]
                        if subsection_index < len(children)
                        else None
                    ),
                )
            else:
                contract = compiled.subsection_contracts[subsection_index - 1]
            sub["event_contract"] = contract.model_dump(mode="json")
    return tree
