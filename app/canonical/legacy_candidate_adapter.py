"""Pure interpretation of legacy Handover payloads as candidate evidence."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import (
    CanonicalEventCandidate,
    FrozenArtifact,
    WorldMutationCandidate,
)
from .hashing import sha256_json


class LegacyHandoverCandidate(FrozenArtifact):
    handover_candidate: dict[str, Any]
    world_mutations: tuple[WorldMutationCandidate, ...] = ()
    events: tuple[CanonicalEventCandidate, ...] = ()
    warnings: tuple[str, ...] = ()


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    return f"{prefix}-{sha256_json(payload)[:24]}"


def _candidate_provenance(
    provenance: dict[str, Any], *, field: str, index: int
) -> dict[str, Any]:
    return {
        **deepcopy(provenance),
        "adapter": "legacy-handover-candidate-v0",
        "legacy_field": field,
        "legacy_index": index,
    }


def _adapt_facts(
    raw: Any, provenance: dict[str, Any]
) -> tuple[list[WorldMutationCandidate], list[str]]:
    mutations: list[WorldMutationCandidate] = []
    warnings: list[str] = []
    if raw is None:
        return mutations, warnings
    if not isinstance(raw, (list, tuple)):
        return mutations, ["new_facts must be a list"]
    for index, item in enumerate(raw):
        candidate_provenance = _candidate_provenance(
            provenance, field="new_facts", index=index
        )
        if isinstance(item, str):
            value = item.strip()
            if not value:
                warnings.append(f"new_facts[{index}] is empty")
                continue
            predicate = "legacy.new_fact"
            subject = "legacy_handover"
            evidence = (value,)
        elif isinstance(item, dict):
            predicate = str(item.get("predicate", "")).strip()
            subject = str(item.get("subject", "")).strip()
            if not predicate or not subject or "value" not in item:
                warnings.append(
                    f"new_facts[{index}] requires predicate, subject and value"
                )
                continue
            value = deepcopy(item["value"])
            raw_evidence = item.get("evidence", ())
            if isinstance(raw_evidence, str):
                evidence = (raw_evidence,)
            elif isinstance(raw_evidence, (list, tuple)):
                evidence = tuple(str(value) for value in raw_evidence)
            else:
                evidence = ()
        else:
            warnings.append(f"new_facts[{index}] has unsupported type")
            continue
        id_payload = {
            "predicate": predicate,
            "subject": subject,
            "value": value,
            "provenance": candidate_provenance,
        }
        mutations.append(
            WorldMutationCandidate(
                mutation_id=_stable_id("legacy-mutation", id_payload),
                predicate=predicate,
                subject=subject,
                value=value,
                provenance=candidate_provenance,
                evidence=evidence,
            )
        )
    return mutations, warnings


def _arc_items(raw: Any) -> list[tuple[str, Any, Any]] | None:
    if isinstance(raw, dict):
        return [(str(arc_id), status, None) for arc_id, status in raw.items()]
    if isinstance(raw, (list, tuple)):
        items = []
        for item in raw:
            if isinstance(item, dict):
                items.append(
                    (
                        str(item.get("arc_id") or item.get("character_id") or ""),
                        item.get("status"),
                        item.get("evidence"),
                    )
                )
            else:
                items.append(("", None, None))
        return items
    if raw is None:
        return []
    return None


def _adapt_arcs(
    raw: Any, provenance: dict[str, Any]
) -> tuple[list[CanonicalEventCandidate], list[str]]:
    events: list[CanonicalEventCandidate] = []
    warnings: list[str] = []
    items = _arc_items(raw)
    if items is None:
        return events, ["arc_progress must be a mapping or list"]
    for index, (arc_id, status, evidence) in enumerate(items):
        if not arc_id:
            warnings.append(f"arc_progress[{index}] is missing arc_id")
            continue
        if status not in {"done", "deviated"}:
            warnings.append(f"arc_progress[{arc_id}] has unsupported status {status!r}")
            continue
        candidate_provenance = _candidate_provenance(
            provenance, field="arc_progress", index=index
        )
        payload = {
            "arc_id": arc_id,
            "status": status,
            "evidence": deepcopy(evidence),
        }
        id_payload = {"payload": payload, "provenance": candidate_provenance}
        events.append(
            CanonicalEventCandidate(
                event_id=_stable_id("legacy-event", id_payload),
                event_type="legacy.arc_progress",
                payload=payload,
                provenance=candidate_provenance,
            )
        )
    return events, warnings


def adapt_legacy_handover(
    handover: dict[str, Any] | None, *, provenance: dict[str, Any]
) -> LegacyHandoverCandidate:
    """Interpret legacy fields without applying any external side effect."""
    candidate = deepcopy(handover or {})
    mutations, fact_warnings = _adapt_facts(candidate.get("new_facts"), provenance)
    events, arc_warnings = _adapt_arcs(candidate.get("arc_progress"), provenance)
    return LegacyHandoverCandidate(
        handover_candidate=candidate,
        world_mutations=tuple(mutations),
        events=tuple(events),
        warnings=tuple([*fact_warnings, *arc_warnings]),
    )
