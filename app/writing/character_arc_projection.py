"""Deterministic projection from outline facts to character-arc candidates.

This module is deliberately offline-only. It translates *what the outline
says* into traceable per-character candidates, but it does not create
production CharacterArc records, EventGraph nodes, Writer context, or hard arc
requirements.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.character_arc_contract import (
    HARD_ARC_TRANSITION,
    OBSERVATIONAL_TEXTURE,
    ORDINARY_PLOT_EVENT,
    SOFT_ARC_PROGRESS,
    UNSUPPORTED_PLANNING_INFERENCE,
    VALID_CLASSIFICATIONS,
)

from .outline_event_contract import ChapterEventContract, OutlineEventUnit


CHARACTER_ARC_PROJECTION_VERSION = "character-arc-projection-v1"

ProjectionStatus = Literal["proposed", "confirmed", "stale", "superseded"]
ProjectionConfidence = Literal["high", "medium", "low"]
ProjectionRequiredness = Literal["hard", "soft", "non_injectable"]

_PROJECTABLE_EVENT_TYPES = {"state_transition", "decision"}
_HARD_FIELDS = (
    "before_state",
    "trigger",
    "after_state",
    "observable_evidence",
    "rationale",
)


def _canonical_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _character_source_hash(character: Mapping[str, object]) -> str:
    return _canonical_hash({
        "id": str(character.get("id") or ""),
        "name": str(character.get("name") or ""),
        "personality": list(character.get("personality") or []),
        "motivation": str(character.get("motivation") or ""),
        "background": str(character.get("background") or ""),
    })


class FrozenProjectionModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ArcProjectionCandidate(FrozenProjectionModel):
    projection_id: str
    character_id: str
    character_name: str
    character_source_hash: str
    event_id: str
    section: int
    subsection: int
    event_type: str
    event_summary: str
    event_text_hash: str
    outline_event_authoritative: bool
    classification: str
    requiredness: ProjectionRequiredness = "non_injectable"
    before_state: str = ""
    trigger: str = ""
    after_state: str = ""
    observable_evidence: str = ""
    rationale: str = ""
    source_id: str
    source_hash: str
    outline_contract_hash: str
    status: ProjectionStatus = "proposed"
    confidence: ProjectionConfidence
    user_confirmed: bool = False
    projection_reason: str
    missing_hard_fields: tuple[str, ...] = ()
    invalidation_reason: str | None = None


class ArcProjectionExclusion(FrozenProjectionModel):
    event_id: str
    section: int
    subsection: int
    reason: str
    source_id: str
    source_hash: str


class CharacterArcProjection(FrozenProjectionModel):
    character_id: str
    character_name: str
    character_source_hash: str
    candidates: tuple[ArcProjectionCandidate, ...]
    projection_hash: str
    confirmed_candidate_ids: tuple[str, ...] = ()
    stale_candidate_ids: tuple[str, ...] = ()
    schema_version: str = CHARACTER_ARC_PROJECTION_VERSION


class ChapterCharacterArcProjection(FrozenProjectionModel):
    section: int
    outline_contract_hash: str
    character_projections: tuple[CharacterArcProjection, ...]
    exclusions: tuple[ArcProjectionExclusion, ...]
    projection_hash: str
    candidate_count: int
    authoritative_candidate_count: int
    hard_candidate_count: int
    stale_candidate_count: int
    schema_version: str = CHARACTER_ARC_PROJECTION_VERSION
    production_effect: bool = False


def _classification_for(event: OutlineEventUnit) -> tuple[str, str]:
    if event.unit_type in _PROJECTABLE_EVENT_TYPES:
        return SOFT_ARC_PROGRESS, "explicit_decision_or_state_transition"
    if event.unit_type == "observation":
        return OBSERVATIONAL_TEXTURE, "observation_is_not_arc_transition"
    if event.unit_type in {
        "action_chain", "dialogue_interaction", "scene_transition"
    }:
        return ORDINARY_PLOT_EVENT, "plot_event_without_explicit_state_transition"
    return UNSUPPORTED_PLANNING_INFERENCE, "event_semantics_unresolved"


def _candidate_payload(candidate: ArcProjectionCandidate) -> dict:
    return candidate.model_dump(mode="json")


class CharacterArcProjector:
    """Build a non-mutating, non-production projection from typed outline facts."""

    def project(
        self,
        *,
        chapter_contract: ChapterEventContract,
        characters: list[Mapping[str, object]],
        prior_projection: Mapping[str, object] | None = None,
    ) -> ChapterCharacterArcProjection:
        character_by_name = {
            str(character.get("name") or ""): character
            for character in characters
            if str(character.get("id") or "") and str(character.get("name") or "")
        }
        outline_hash_by_event: dict[str, str] = {}
        events: list[OutlineEventUnit] = []
        exclusions: list[ArcProjectionExclusion] = []
        for contract in chapter_contract.subsection_contracts:
            for event in contract.events:
                if event.status == "superseded":
                    continue
                outline_hash_by_event[event.event_id] = contract.contract_hash
                events.append(event)

        proposed: list[ArcProjectionCandidate] = []
        for event in events:
            matched = [
                character_by_name[name]
                for name in event.actors
                if name in character_by_name
            ]
            if not matched:
                exclusions.append(ArcProjectionExclusion(
                    event_id=event.event_id,
                    section=event.section,
                    subsection=event.subsection,
                    reason=(
                        "no_exact_character_actor"
                        if not event.actors
                        else "actor_not_in_character_roster"
                    ),
                    source_id=event.source_id,
                    source_hash=event.source_hash,
                ))
                continue
            classification, reason = _classification_for(event)
            for character in matched:
                character_id = str(character.get("id"))
                character_name = str(character.get("name"))
                source_confirmed = (
                    event.status == "confirmed" and event.user_confirmed
                )
                proposed.append(ArcProjectionCandidate(
                    projection_id=(
                        f"arcproj:{character_id}:{event.event_id}"
                    ),
                    character_id=character_id,
                    character_name=character_name,
                    character_source_hash=_character_source_hash(character),
                    event_id=event.event_id,
                    section=event.section,
                    subsection=event.subsection,
                    event_type=event.unit_type,
                    event_summary=event.summary,
                    event_text_hash=event.text_hash,
                    outline_event_authoritative=source_confirmed,
                    classification=classification,
                    requiredness="non_injectable",
                    source_id=event.source_id,
                    source_hash=event.source_hash,
                    outline_contract_hash=outline_hash_by_event[event.event_id],
                    confidence=(
                        "medium"
                        if source_confirmed and classification == SOFT_ARC_PROGRESS
                        else "low"
                    ),
                    projection_reason=(
                        reason
                        if source_confirmed
                        else f"{reason}:outline_event_not_author_confirmed"
                    ),
                    missing_hard_fields=(
                        _HARD_FIELDS
                        if classification == SOFT_ARC_PROGRESS
                        else ()
                    ),
                ))

        reconciled = self._reconcile(proposed, prior_projection)
        grouped: dict[str, list[ArcProjectionCandidate]] = {
            str(character.get("id")): []
            for character in characters
            if str(character.get("id") or "")
        }
        for candidate in reconciled:
            grouped.setdefault(candidate.character_id, []).append(candidate)

        character_records = {
            str(character.get("id")): character
            for character in characters
            if str(character.get("id") or "")
        }
        character_order = list(character_records)
        character_order.extend(
            character_id
            for character_id in sorted(grouped)
            if character_id not in character_records
        )
        projections: list[CharacterArcProjection] = []
        for character_id in character_order:
            character = character_records.get(character_id)
            candidates = tuple(sorted(
                grouped.get(character_id, []),
                key=lambda item: (
                    item.section, item.subsection, item.event_id, item.projection_id
                ),
            ))
            payload = {
                "character_id": character_id,
                "character_source_hash": (
                    _character_source_hash(character)
                    if character is not None
                    else (candidates[0].character_source_hash if candidates else "")
                ),
                "candidates": [_candidate_payload(item) for item in candidates],
            }
            projections.append(CharacterArcProjection(
                character_id=character_id,
                character_name=(
                    str(character.get("name") or "")
                    if character is not None
                    else (candidates[0].character_name if candidates else "")
                ),
                character_source_hash=payload["character_source_hash"],
                candidates=candidates,
                projection_hash=_canonical_hash(payload),
                confirmed_candidate_ids=tuple(
                    item.projection_id for item in candidates
                    if item.status == "confirmed"
                ),
                stale_candidate_ids=tuple(
                    item.projection_id for item in candidates
                    if item.status == "stale"
                ),
            ))

        all_candidates = [
            candidate
            for projection in projections
            for candidate in projection.candidates
        ]
        payload = {
            "section": chapter_contract.section,
            "outline_contract_hash": chapter_contract.contract_hash,
            "character_projection_hashes": [
                projection.projection_hash for projection in projections
            ],
            "exclusions": [
                exclusion.model_dump(mode="json") for exclusion in exclusions
            ],
            "schema_version": CHARACTER_ARC_PROJECTION_VERSION,
        }
        return ChapterCharacterArcProjection(
            section=chapter_contract.section,
            outline_contract_hash=chapter_contract.contract_hash,
            character_projections=tuple(projections),
            exclusions=tuple(exclusions),
            projection_hash=_canonical_hash(payload),
            candidate_count=len(all_candidates),
            authoritative_candidate_count=sum(
                item.status == "confirmed" and item.user_confirmed
                for item in all_candidates
            ),
            hard_candidate_count=sum(
                item.classification == HARD_ARC_TRANSITION
                and item.requiredness == "hard"
                and item.status == "confirmed"
                for item in all_candidates
            ),
            stale_candidate_count=sum(
                item.status == "stale" for item in all_candidates
            ),
        )

    def confirm_candidate(
        self,
        *,
        candidate: ArcProjectionCandidate,
        submitted: Mapping[str, object],
    ) -> ArcProjectionCandidate:
        """Apply an explicit author/editor decision without guessing state fields."""
        if str(submitted.get("projection_id") or "") != candidate.projection_id:
            raise ValueError("projection_id_mismatch")
        if str(submitted.get("event_text_hash") or "") != candidate.event_text_hash:
            raise ValueError("event_source_changed")
        if not candidate.outline_event_authoritative:
            raise ValueError("outline_event_not_confirmed")
        classification = str(submitted.get("classification") or "")
        if classification not in VALID_CLASSIFICATIONS:
            raise ValueError("invalid_classification")
        updates = {
            field: str(submitted.get(field) or "").strip()
            for field in _HARD_FIELDS
        }
        requiredness: ProjectionRequiredness
        missing: tuple[str, ...] = ()
        if classification == HARD_ARC_TRANSITION:
            missing = tuple(field for field, value in updates.items() if not value)
            if (
                candidate.event_type not in _PROJECTABLE_EVENT_TYPES
                or missing
            ):
                classification = SOFT_ARC_PROGRESS
                requiredness = "soft"
            else:
                requiredness = "hard"
        elif classification == SOFT_ARC_PROGRESS:
            requiredness = "soft"
        else:
            requiredness = "non_injectable"
        return candidate.model_copy(update={
            "classification": classification,
            "requiredness": requiredness,
            **updates,
            "status": "confirmed",
            "confidence": "high",
            "user_confirmed": True,
            "projection_reason": "author_confirmed_projection",
            "missing_hard_fields": missing,
            "invalidation_reason": None,
        })

    @staticmethod
    def _reconcile(
        proposed: list[ArcProjectionCandidate],
        prior_projection: Mapping[str, object] | None,
    ) -> list[ArcProjectionCandidate]:
        if not prior_projection:
            return proposed
        try:
            prior = ChapterCharacterArcProjection.model_validate(prior_projection)
        except Exception:
            return proposed
        prior_by_id = {
            candidate.projection_id: candidate
            for projection in prior.character_projections
            for candidate in projection.candidates
        }
        result: list[ArcProjectionCandidate] = []
        active_ids: set[str] = set()
        for item in proposed:
            active_ids.add(item.projection_id)
            old = prior_by_id.get(item.projection_id)
            if old is None or old.status != "confirmed":
                result.append(item)
                continue
            same_source = (
                old.event_text_hash == item.event_text_hash
                and old.source_hash == item.source_hash
                and old.character_source_hash == item.character_source_hash
            )
            if same_source:
                result.append(old.model_copy(update={
                    "outline_contract_hash": item.outline_contract_hash,
                }))
            else:
                result.append(item.model_copy(update={
                    "status": "stale",
                    "user_confirmed": False,
                    "requiredness": "non_injectable",
                    "confidence": "low",
                    "invalidation_reason": "event_or_character_source_changed",
                }))
        for projection in prior.character_projections:
            for old in projection.candidates:
                if old.projection_id in active_ids:
                    continue
                result.append(old.model_copy(update={
                    "status": "superseded",
                    "user_confirmed": False,
                    "requiredness": "non_injectable",
                    "confidence": "low",
                    "invalidation_reason": "source_event_removed",
                }))
        return result


def iter_projection_candidates(
    projection: ChapterCharacterArcProjection,
) -> Iterable[ArcProjectionCandidate]:
    for character_projection in projection.character_projections:
        yield from character_projection.candidates


def export_confirmed_v2_arcs(
    projection: ChapterCharacterArcProjection,
) -> list[dict]:
    """Export only explicit confirmations; never called by production in V1."""
    result: list[dict] = []
    for character_projection in projection.character_projections:
        milestones = []
        for candidate in character_projection.candidates:
            if candidate.status != "confirmed" or not candidate.user_confirmed:
                continue
            if candidate.classification not in {
                HARD_ARC_TRANSITION, SOFT_ARC_PROGRESS
            }:
                continue
            milestones.append({
                "milestone_id": candidate.projection_id,
                "section": candidate.section,
                "subsection": candidate.subsection,
                "event": candidate.event_summary,
                "classification": candidate.classification,
                "requiredness": candidate.requiredness,
                "before_state": candidate.before_state,
                "trigger": candidate.trigger,
                "after_state": candidate.after_state,
                "observable_evidence": candidate.observable_evidence,
                "source_id": candidate.source_id,
                "source_hash": candidate.source_hash,
                "rationale": candidate.rationale,
                "contract_version": "v2",
            })
        if milestones:
            result.append({
                "character_id": character_projection.character_id,
                "key_milestones": milestones,
                "projection_version": CHARACTER_ARC_PROJECTION_VERSION,
            })
    return result
