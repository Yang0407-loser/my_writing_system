"""Deterministic StateFrame V1 construction and source adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .state_frame_v1 import (
    FactChange,
    StateDelta,
    StateExpectation,
    StateFact,
    StateFrameSnapshot,
    StateSourceRef,
    canonical_hash,
    task_id_hash,
)


_CATEGORY_TO_FACT_TYPE = {
    "character_state": "character_state",
    "relationship": "relationship_state",
    "temporal_state": "temporal_state",
    "location_state": "location_state",
    "character_presence": "presence_state",
    "handover": "continuity_state",
    "event": "open_event_chain",
    "foreshadowing": "foreshadow_state",
}


def _confidence(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _fact_id(payload: Mapping[str, Any]) -> str:
    supplied = str(payload.get("fact_id") or payload.get("change_id") or "")
    if supplied:
        return supplied
    identity = {
        "fact_type": payload.get("fact_type"),
        "subject": payload.get("subject"),
        "predicate": payload.get("predicate"),
        "source_id": payload.get("source_id"),
    }
    return f"statefact:{canonical_hash(identity)[:20]}"


def fact_from_mapping(payload: Mapping[str, Any]) -> StateFact:
    evidence = payload.get("evidence")
    first_evidence = (
        evidence[0]
        if isinstance(evidence, (list, tuple)) and evidence and isinstance(evidence[0], Mapping)
        else {}
    )
    source_id = str(
        payload.get("source_id")
        or first_evidence.get("source_id")
        or "source:unavailable"
    )
    source_hash = str(
        payload.get("source_hash")
        or payload.get("text_hash")
        or first_evidence.get("text_hash")
        or canonical_hash({"source_id": source_id})
    )
    fact_type = str(
        payload.get("fact_type")
        or _CATEGORY_TO_FACT_TYPE.get(str(payload.get("category")), "continuity_state")
    )
    status = str(payload.get("status") or "unknown")
    if status not in {"confirmed", "unknown", "conflicted", "pending"}:
        status = "unknown"
    return StateFact(
        fact_id=_fact_id({**payload, "fact_type": fact_type, "source_id": source_id}),
        fact_type=fact_type,
        subject=str(payload.get("subject") or payload.get("name") or "scene"),
        predicate=str(payload.get("predicate") or "reported_state"),
        value=payload.get("value"),
        status=status,
        durability=str(payload.get("durability") or "subsection"),
        valid_from=payload.get("valid_from"),
        valid_until=payload.get("valid_until"),
        section=payload.get("section"),
        subsection=payload.get("subsection"),
        source_type=str(payload.get("source_type") or payload.get("category") or "unknown"),
        source_id=source_id,
        source_hash=source_hash,
        evidence_start=payload.get("evidence_start", first_evidence.get("span_start")),
        evidence_end=payload.get("evidence_end", first_evidence.get("span_end")),
        evidence_excerpt=str(
            payload.get("evidence_excerpt") or first_evidence.get("excerpt") or ""
        )[:140],
        producer=str(payload.get("producer") or "existing_state_source"),
        confidence=_confidence(payload.get("confidence")),
        provenance=str(payload.get("provenance") or "extractor_reported"),
    )


def facts_from_post_write_bundle(bundle: Mapping[str, Any]) -> list[StateFact]:
    section = int(bundle.get("section") or 0)
    subsection = int(bundle.get("subsection") or 0)
    facts: list[StateFact] = []
    for raw in bundle.get("changes") or []:
        if not isinstance(raw, Mapping):
            continue
        facts.append(fact_from_mapping({
            **raw,
            "section": section,
            "subsection": subsection,
            "source_type": "post_write_state_bundle",
            "producer": "SharedPostWriteExtractor",
            "provenance": "extractor_reported",
        }))
    return facts


def facts_from_handover(
    handovers: Iterable[Mapping[str, Any]], *, section: int
) -> list[StateFact]:
    facts: list[StateFact] = []
    for index, note in enumerate(handovers):
        if int(note.get("to_section") or 0) != section:
            continue
        source_id = str(
            note.get("source_id")
            or f"handover:{note.get('from_section', 0)}:{section}:{index}"
        )
        for field, fact_type, predicate, durability in (
            ("character_state", "continuity_state", "handover_character_state", "chapter"),
            ("open_threads", "open_event_chain", "handover_open_thread", "until_resolved"),
            ("foreshadowing", "foreshadow_state", "handover_foreshadow", "until_resolved"),
        ):
            value = note.get(field)
            if not value or str(value).strip() in {"无", "none", "None"}:
                continue
            facts.append(fact_from_mapping({
                "fact_type": fact_type,
                "subject": "handover",
                "predicate": predicate,
                "value": value,
                "status": "pending" if field != "character_state" else "unknown",
                "durability": durability,
                "section": section,
                "source_type": "handover",
                "source_id": f"{source_id}:{field}",
                "source_hash": canonical_hash({"field": field, "value": value}),
                "producer": "Writer._extract_handover",
                "confidence": 0.0,
                "provenance": "unstructured_handover_reported",
            }))
    return facts


def facts_from_relations(relations: Iterable[Mapping[str, Any]]) -> list[StateFact]:
    facts = []
    for relation in relations:
        relation_id = str(relation.get("id") or canonical_hash(relation)[:16])
        stage_index = int(relation.get("current_stage") or 0)
        stages = relation.get("stages") or []
        stage = stages[stage_index] if 0 <= stage_index < len(stages) else None
        value = stage or {
            "relation_type": relation.get("relation_type"),
            "direction": relation.get("direction"),
            "intensity": relation.get("intensity"),
        }
        facts.append(fact_from_mapping({
            "fact_type": "relationship_state",
            "subject": "|".join(sorted([
                str(relation.get("character_a") or ""),
                str(relation.get("character_b") or ""),
            ])),
            "predicate": "relationship_stage",
            "value": value,
            "status": "confirmed",
            "durability": "persistent",
            "section": relation.get("source_section"),
            "source_type": "character_relation_store",
            "source_id": relation_id,
            "source_hash": canonical_hash(relation),
            "producer": "character_relation_store",
            "confidence": 1.0,
            "provenance": "authoritative_current_store_snapshot",
        }))
    return facts


def facts_from_foreshadows(items: Iterable[Mapping[str, Any]]) -> list[StateFact]:
    facts = []
    for item in items:
        source_id = str(item.get("id") or item.get("foreshadow_id") or canonical_hash(item)[:16])
        status = str(item.get("status") or "pending")
        facts.append(fact_from_mapping({
            "fact_type": "foreshadow_state",
            "subject": source_id,
            "predicate": "foreshadow_lifecycle",
            "value": {
                "status": status,
                "plant_chapter": item.get("plant_chapter"),
                "resolve_chapter": item.get("resolve_chapter"),
                "invalid_resolve_chapter": bool(
                    item.get("_invalid_resolve_chapter", False)
                ),
            },
            "status": "confirmed",
            "durability": "until_resolved",
            "section": item.get("plant_chapter"),
            "source_type": "foreshadowing_store",
            "source_id": source_id,
            "source_hash": canonical_hash(item),
            "producer": "foreshadowing_store",
            "confidence": 1.0,
            "provenance": "authoritative_current_store_snapshot",
        }))
    return facts


def expectations_from_outline_contract(
    contracts: Iterable[Mapping[str, Any]], *, section: int, subsection: int
) -> list[StateExpectation]:
    expectations: list[StateExpectation] = []
    for contract in contracts:
        if (
            int(contract.get("section") or 0) != section
            or int(contract.get("subsection") or 0) != subsection
        ):
            continue
        required_ids = set(contract.get("required_event_ids") or [])
        for event in contract.get("events") or []:
            event_id = str(event.get("event_id") or "")
            if not event_id:
                continue
            event_type = str(event.get("unit_type") or "event")
            requiredness = (
                "hard" if event_id in required_ids
                else "observational" if event_type == "observation"
                else "soft"
            )
            actors = event.get("actors") or []
            expectations.append(StateExpectation(
                expectation_id=f"expectation:{event_id}",
                expectation_type=event_type,
                subject="|".join(str(value) for value in actors) or "unassigned",
                expected_transition=str(event.get("summary") or ""),
                requiredness=requiredness,
                section=section,
                subsection=subsection,
                source_id=str(event.get("source_id") or event_id),
                source_hash=str(event.get("source_hash") or canonical_hash(event)),
                confidence={"high": 1.0, "medium": 0.6, "low": 0.3}.get(
                    str(event.get("confidence")), 0.0
                ),
                provenance=(
                    "author_confirmed_outline_event"
                    if event.get("status") == "confirmed" and event.get("user_confirmed")
                    else "planned_outline_event"
                ),
            ))
    return expectations


class StateFrameBuilder:
    def build(
        self,
        *,
        task_id: str,
        section: int,
        subsection: int,
        frame_phase: str,
        facts: Iterable[StateFact],
        expectations: Iterable[StateExpectation] = (),
        source_manifest: Iterable[StateSourceRef] = (),
        pending_source_types: Iterable[str] = (),
        unavailable_source_types: Iterable[str] = (),
        checkpoint_id: str | None = None,
        checkpoint_version: str | None = None,
    ) -> StateFrameSnapshot:
        ordered_facts = tuple(sorted(
            facts,
            key=lambda item: (item.fact_type, item.subject, item.predicate, item.fact_id),
        ))
        ordered_expectations = tuple(sorted(
            expectations, key=lambda item: item.expectation_id
        ))
        manifest = tuple(sorted(
            source_manifest,
            key=lambda item: (item.source_type, item.source_id, item.source_hash),
        ))
        pending = tuple(sorted(set(pending_source_types)))
        unavailable = tuple(sorted(set(unavailable_source_types)))
        if pending:
            status = "pending_sources"
        elif not ordered_facts and not ordered_expectations:
            status = "unavailable"
        elif unavailable:
            status = "partial"
        else:
            status = "complete"
        body = {
            "task_id_hash": task_id_hash(task_id),
            "section": section,
            "subsection": subsection,
            "checkpoint_id": checkpoint_id,
            "checkpoint_version": checkpoint_version,
            "frame_phase": frame_phase,
            "frame_status": status,
            "facts": [item.model_dump(mode="json") for item in ordered_facts],
            "expectations": [
                item.model_dump(mode="json") for item in ordered_expectations
            ],
            "pending_source_types": list(pending),
            "unavailable_source_types": list(unavailable),
            "source_manifest": [item.model_dump(mode="json") for item in manifest],
            "conflicts": sorted(
                item.fact_id for item in ordered_facts if item.status == "conflicted"
            ),
        }
        frame_hash = canonical_hash(body)
        return StateFrameSnapshot(
            frame_id=(
                f"stateframe:{task_id_hash(task_id)[:16]}:"
                f"S{section}.{subsection}:{frame_phase}"
            ),
            **body,
            created_at=None,
            finalized_at=None if status == "pending_sources" else "source_snapshot",
            frame_hash=frame_hash,
        )

    def finalize(
        self, frame: StateFrameSnapshot, *, additional_facts: Iterable[StateFact]
    ) -> StateFrameSnapshot:
        ordered_facts = tuple(sorted(
            (*frame.facts, *additional_facts),
            key=lambda item: (item.fact_type, item.subject, item.predicate, item.fact_id),
        ))
        body = {
            "task_id_hash": frame.task_id_hash,
            "section": frame.section,
            "subsection": frame.subsection,
            "checkpoint_id": frame.checkpoint_id,
            "checkpoint_version": frame.checkpoint_version,
            "frame_phase": frame.frame_phase,
            "frame_status": "complete",
            "facts": [item.model_dump(mode="json") for item in ordered_facts],
            "expectations": [
                item.model_dump(mode="json") for item in frame.expectations
            ],
            "pending_source_types": [],
            "unavailable_source_types": [],
            "source_manifest": [
                item.model_dump(mode="json") for item in frame.source_manifest
            ],
            "conflicts": sorted(
                item.fact_id for item in ordered_facts if item.status == "conflicted"
            ),
        }
        return StateFrameSnapshot(
            frame_id=frame.frame_id,
            **body,
            created_at=frame.created_at,
            finalized_at="source_snapshot",
            frame_hash=canonical_hash(body),
        )

    @staticmethod
    def delta(before: StateFrameSnapshot, after: StateFrameSnapshot) -> StateDelta:
        before_map = {item.identity_key: item for item in before.facts}
        after_map = {item.identity_key: item for item in after.facts}
        added: list[StateFact] = []
        changed: list[FactChange] = []
        resolved: list[StateFact] = []
        unchanged: list[StateFact] = []
        for key in sorted(set(before_map) | set(after_map)):
            prior = before_map.get(key)
            current = after_map.get(key)
            if prior is None and current is not None:
                added.append(current)
            elif current is None and prior is not None:
                resolved.append(prior)
            elif prior is not None and current is not None:
                if (
                    prior.value == current.value
                    and prior.status == current.status
                    and prior.valid_until == current.valid_until
                ):
                    unchanged.append(current)
                else:
                    changed.append(FactChange(before=prior, after=current))
        resolved_expectations = tuple(sorted(
            item.expectation_id
            for item in after.expectations
            if item.status == "supported"
        ))
        unresolved_expectations = tuple(sorted(
            item.expectation_id
            for item in after.expectations
            if item.status in {"planned", "partially_supported", "unassessable"}
        ))
        body = {
            "before_frame_hash": before.frame_hash,
            "after_frame_hash": after.frame_hash,
            "added_facts": [item.model_dump(mode="json") for item in added],
            "changed_facts": [item.model_dump(mode="json") for item in changed],
            "resolved_facts": [item.model_dump(mode="json") for item in resolved],
            "unchanged_facts": [item.model_dump(mode="json") for item in unchanged],
            "new_conflicts": sorted(set(after.conflicts) - set(before.conflicts)),
            "resolved_expectations": list(resolved_expectations),
            "unresolved_expectations": list(unresolved_expectations),
        }
        return StateDelta(
            delta_id=f"statedelta:{canonical_hash(body)[:24]}",
            **body,
        )
