"""Read-only, non-authoritative view over existing post-write artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ADAPTER_VERSION = "subsection-outcome-adapter-v1"
BUNDLE_SCHEMA_VERSION = "subsection-outcome-bundle-v1"

OutcomeGranularity = Literal[
    "subsection_exact",
    "section_aggregate",
    "current_store_snapshot",
    "task_final_snapshot",
    "unknown_granularity",
]
OutcomeAuthority = Literal[
    "authoritative_store",
    "committed_checkpoint",
    "blackboard_runtime",
    "derived_read_only_view",
    "experimental_shadow",
]
OutcomeComponentType = Literal[
    "handover_delta",
    "character_state_delta",
    "relationship_delta",
    "foreshadow_delta",
    "experience_delta",
]
OutcomeAvailability = Literal[
    "available",
    "partial",
    "unavailable",
    "conflicted",
    "error",
]
TemporalIntegrityStatus = Literal[
    "verified",
    "partial",
    "unverifiable",
    "conflicted",
]


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_task_id(task_id: str) -> str:
    return hashlib.sha256(str(task_id).encode("utf-8")).hexdigest()


class FrozenOutcomeArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OutcomeSourceRef(FrozenOutcomeArtifact):
    source_type: str
    source_id: str
    source_hash: str
    producer: str
    storage_location: str
    section: int | None = None
    subsection: int | None = None
    granularity: OutcomeGranularity
    captured_at: str | None = None
    authority: OutcomeAuthority
    provenance: str

    @model_validator(mode="after")
    def validate_identity(self) -> "OutcomeSourceRef":
        if not self.source_id or not self.source_hash:
            raise ValueError("source_id_and_hash_required")
        if self.granularity == "subsection_exact":
            if not self.section or not self.subsection:
                raise ValueError("subsection_exact_requires_boundary")
        return self


class OutcomeComponent(FrozenOutcomeArtifact):
    component_type: OutcomeComponentType
    availability: OutcomeAvailability
    granularity: OutcomeGranularity | None = None
    summary_hash: str
    source_refs: tuple[OutcomeSourceRef, ...] = ()
    item_count: int = Field(ge=0)
    unavailable_reason: str | None = None
    conflict_reason: str | None = None
    producer_status: str
    production_effect: bool = False

    @model_validator(mode="after")
    def validate_availability(self) -> "OutcomeComponent":
        if self.availability in {"available", "partial", "conflicted"}:
            if not self.source_refs:
                raise ValueError("claimed_component_requires_source")
        if self.availability == "available":
            if self.granularity != "subsection_exact":
                raise ValueError("available_requires_subsection_exact")
        if self.availability == "unavailable":
            if self.source_refs:
                raise ValueError("unavailable_must_not_fabricate_source")
            if self.granularity is not None:
                raise ValueError("unavailable_must_not_claim_granularity")
            if not self.unavailable_reason:
                raise ValueError("unavailable_reason_required")
        if self.item_count != len(self.source_refs):
            raise ValueError("item_count_must_match_source_refs")
        return self


class SubsectionOutcomeBundle(FrozenOutcomeArtifact):
    schema_version: str = BUNDLE_SCHEMA_VERSION
    bundle_id: str
    task_id_hash: str
    section: int = Field(ge=1)
    subsection: int = Field(ge=1)
    output_sha256: str
    prompt_messages_hash: str
    commit_idempotency_key: str
    components: tuple[OutcomeComponent, ...]
    source_manifest: tuple[OutcomeSourceRef, ...]
    available_component_count: int = Field(ge=0)
    partial_component_count: int = Field(ge=0)
    unavailable_component_count: int = Field(ge=0)
    exact_subsection_component_count: int = Field(ge=0)
    source_traceability_rate: float = Field(ge=0.0, le=1.0)
    temporal_integrity_status: TemporalIntegrityStatus
    bundle_hash: str
    production_effect: bool = False
    adapter_version: str = ADAPTER_VERSION


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _stable_item_id(
    item: Mapping[str, Any],
    *,
    prefix: str,
    task_id_hash: str,
    index: int,
) -> str:
    for key in ("source_id", "id", "event_id", "record_id", "change_id"):
        if item.get(key):
            return str(item[key])
    return f"{prefix}:{task_id_hash[:16]}:{index}:{canonical_hash(item)[:16]}"


def _source_ref(
    item: Mapping[str, Any],
    *,
    source_type: str,
    source_id: str,
    producer: str,
    storage_location: str,
    section: int | None,
    subsection: int | None,
    granularity: OutcomeGranularity,
    authority: OutcomeAuthority,
    provenance: str,
) -> OutcomeSourceRef:
    captured_at = item.get("captured_at") or item.get("updated_at") or item.get(
        "created_at"
    )
    return OutcomeSourceRef(
        source_type=source_type,
        source_id=source_id,
        source_hash=canonical_hash(item),
        producer=producer,
        storage_location=storage_location,
        section=section,
        subsection=subsection,
        granularity=granularity,
        captured_at=str(captured_at) if captured_at else None,
        authority=authority,
        provenance=provenance,
    )


def _unavailable(
    component_type: OutcomeComponentType, reason: str
) -> OutcomeComponent:
    return OutcomeComponent(
        component_type=component_type,
        availability="unavailable",
        granularity=None,
        summary_hash=canonical_hash(
            {"component_type": component_type, "unavailable_reason": reason}
        ),
        source_refs=(),
        item_count=0,
        unavailable_reason=reason,
        producer_status="unavailable",
        production_effect=False,
    )


def _component_from_refs(
    component_type: OutcomeComponentType,
    refs: Sequence[OutcomeSourceRef],
    *,
    availability: OutcomeAvailability,
    granularity: OutcomeGranularity,
    producer_status: str,
    conflict_reason: str | None = None,
) -> OutcomeComponent:
    ordered = tuple(sorted(refs, key=lambda item: (item.source_id, item.source_hash)))
    hashes_by_id: dict[str, set[str]] = {}
    for ref in ordered:
        hashes_by_id.setdefault(ref.source_id, set()).add(ref.source_hash)
    conflicts = sorted(
        source_id
        for source_id, hashes in hashes_by_id.items()
        if len(hashes) > 1
    )
    if conflicts:
        availability = "conflicted"
        conflict_reason = "same_source_id_with_different_hash:" + ",".join(conflicts)
        producer_status = "conflicted"
    summary = [
        {
            "source_id": ref.source_id,
            "source_hash": ref.source_hash,
            "granularity": ref.granularity,
            "storage_location": ref.storage_location,
        }
        for ref in ordered
    ]
    return OutcomeComponent(
        component_type=component_type,
        availability=availability,
        granularity=granularity,
        summary_hash=canonical_hash(summary),
        source_refs=ordered,
        item_count=len(ordered),
        conflict_reason=conflict_reason,
        producer_status=producer_status,
        production_effect=False,
    )


class SubsectionOutcomeBundleAdapter:
    """Map already persisted artifacts without mutating or re-running producers."""

    def build(
        self,
        *,
        task_id: str,
        section: int,
        subsection: int,
        state_frame_record: Mapping[str, Any],
        handover_entries: Sequence[Mapping[str, Any]] = (),
        character_state_records: Sequence[Mapping[str, Any]] = (),
        relationship_records: Sequence[Mapping[str, Any]] = (),
        foreshadow_records: Sequence[Mapping[str, Any]] = (),
        experience_records: Sequence[Mapping[str, Any]] = (),
        is_last_subsection: bool = False,
    ) -> SubsectionOutcomeBundle:
        record = _mapping(state_frame_record)
        output_hash = str(record.get("output_sha256") or "")
        prompt_hash = str(record.get("prompt_messages_hash") or "")
        commit_key = str(record.get("commit_idempotency_key") or "")
        if not output_hash or not prompt_hash or not commit_key:
            raise ValueError("committed_state_frame_record_required")

        task_hash = hash_task_id(task_id)
        components = (
            self._handover_component(
                task_hash, section, subsection, handover_entries, is_last_subsection
            ),
            self._character_component(
                task_hash,
                section,
                subsection,
                character_state_records,
                is_last_subsection,
            ),
            self._relationship_component(
                task_hash,
                section,
                subsection,
                relationship_records,
                is_last_subsection,
            ),
            self._foreshadow_component(
                task_hash,
                section,
                subsection,
                foreshadow_records,
                is_last_subsection,
            ),
            self._experience_component(
                task_hash,
                section,
                subsection,
                experience_records,
                is_last_subsection,
            ),
        )
        state_ref = OutcomeSourceRef(
            source_type="state_frame_history",
            source_id=str(record.get("record_id") or f"state-frame:S{section}.{subsection}"),
            source_hash=canonical_hash(record),
            producer="StateFrameHistoryRecorder",
            storage_location="task_history.analysis_json.state_frame_history_v1",
            section=section,
            subsection=subsection,
            granularity="subsection_exact",
            captured_at=record.get("finalized_at"),
            authority="committed_checkpoint",
            provenance="persisted_committed_boundary",
        )
        refs_by_identity = {
            (ref.source_type, ref.source_id, ref.source_hash): ref
            for component in components
            for ref in component.source_refs
        }
        refs_by_identity[
            (state_ref.source_type, state_ref.source_id, state_ref.source_hash)
        ] = state_ref
        manifest = tuple(
            sorted(
                refs_by_identity.values(),
                key=lambda item: (item.source_type, item.source_id, item.source_hash),
            )
        )
        traceable = sum(bool(ref.source_id and ref.source_hash) for ref in manifest)
        availability_counts = {
            name: sum(component.availability == name for component in components)
            for name in ("available", "partial", "unavailable")
        }
        if any(component.availability == "conflicted" for component in components):
            temporal_status: TemporalIntegrityStatus = "conflicted"
        elif any(
            component.availability == "partial"
            or component.granularity
            in {
                "section_aggregate",
                "current_store_snapshot",
                "task_final_snapshot",
                "unknown_granularity",
            }
            for component in components
        ):
            temporal_status = "partial"
        else:
            temporal_status = "verified"

        bundle_id = (
            f"subsection-outcome:{task_hash}:S{section}.{subsection}:{output_hash}"
        )
        body = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "bundle_id": bundle_id,
            "task_id_hash": task_hash,
            "section": section,
            "subsection": subsection,
            "output_sha256": output_hash,
            "prompt_messages_hash": prompt_hash,
            "commit_idempotency_key": commit_key,
            "components": [
                component.model_dump(mode="json") for component in components
            ],
            "source_manifest": [ref.model_dump(mode="json") for ref in manifest],
            "available_component_count": availability_counts["available"],
            "partial_component_count": availability_counts["partial"],
            "unavailable_component_count": availability_counts["unavailable"],
            "exact_subsection_component_count": sum(
                component.availability == "available"
                and component.granularity == "subsection_exact"
                for component in components
            ),
            "source_traceability_rate": (
                traceable / len(manifest) if manifest else 1.0
            ),
            "temporal_integrity_status": temporal_status,
            "production_effect": False,
            "adapter_version": ADAPTER_VERSION,
        }
        return SubsectionOutcomeBundle(
            **body,
            bundle_hash=canonical_hash(body),
        )

    @staticmethod
    def _handover_component(
        task_hash: str,
        section: int,
        subsection: int,
        entries: Sequence[Mapping[str, Any]],
        is_last: bool,
    ) -> OutcomeComponent:
        exact = [
            _mapping(item)
            for item in entries
            if _positive_int(item.get("from_section") or item.get("section")) == section
            and _positive_int(item.get("subsection")) == subsection
        ]
        if exact:
            refs = [
                _source_ref(
                    item,
                    source_type="handover",
                    source_id=_stable_item_id(
                        item, prefix="handover", task_id_hash=task_hash, index=index
                    ),
                    producer="Writer._extract_handover",
                    storage_location="checkpoint.handover_chain",
                    section=section,
                    subsection=subsection,
                    granularity="subsection_exact",
                    authority="committed_checkpoint",
                    provenance="explicit_subsection_boundary",
                )
                for index, item in enumerate(exact, 1)
            ]
            return _component_from_refs(
                "handover_delta",
                refs,
                availability="available",
                granularity="subsection_exact",
                producer_status="completed",
            )
        aggregate = [
            _mapping(item)
            for item in entries
            if _positive_int(item.get("from_section") or item.get("section")) == section
        ]
        if is_last and aggregate:
            refs = [
                _source_ref(
                    item,
                    source_type="handover",
                    source_id=_stable_item_id(
                        item,
                        prefix="handover-section",
                        task_id_hash=task_hash,
                        index=index,
                    ),
                    producer="StateCommitter.commit_section_handover",
                    storage_location="task_history.handover_json",
                    section=section,
                    subsection=None,
                    granularity="section_aggregate",
                    authority="committed_checkpoint",
                    provenance="section_end_aggregate_not_subsection_delta",
                )
                for index, item in enumerate(aggregate, 1)
            ]
            return _component_from_refs(
                "handover_delta",
                refs,
                availability="partial",
                granularity="section_aggregate",
                producer_status="section_aggregate_only",
            )
        return _unavailable(
            "handover_delta", "no_persisted_subsection_handover_artifact"
        )

    @staticmethod
    def _character_component(
        task_hash: str,
        section: int,
        subsection: int,
        records: Sequence[Mapping[str, Any]],
        is_last: bool,
    ) -> OutcomeComponent:
        exact = [
            _mapping(item)
            for item in records
            if _positive_int(item.get("section")) == section
            and _positive_int(item.get("subsection")) == subsection
            and item.get("input_state_hash")
            and item.get("updated_state_hash")
        ]
        if exact:
            refs = [
                _source_ref(
                    item,
                    source_type="character_state",
                    source_id=_stable_item_id(
                        item,
                        prefix="character-state",
                        task_id_hash=task_hash,
                        index=index,
                    ),
                    producer="CharacterManager.update_states",
                    storage_location="checkpoint._character_state_propagation",
                    section=section,
                    subsection=subsection,
                    granularity="subsection_exact",
                    authority="committed_checkpoint",
                    provenance="explicit_before_after_state_hashes",
                )
                for index, item in enumerate(exact, 1)
            ]
            return _component_from_refs(
                "character_state_delta",
                refs,
                availability="available",
                granularity="subsection_exact",
                producer_status="completed",
            )
        if is_last and records:
            refs = [
                _source_ref(
                    _mapping(item),
                    source_type="character_state",
                    source_id=_stable_item_id(
                        _mapping(item),
                        prefix="character-final",
                        task_id_hash=task_hash,
                        index=index,
                    ),
                    producer="Writer/Coordinator character state propagation",
                    storage_location="task_history.characters_json",
                    section=None,
                    subsection=None,
                    granularity="task_final_snapshot",
                    authority="authoritative_store",
                    provenance="task_final_snapshot_not_subsection_delta",
                )
                for index, item in enumerate(records, 1)
            ]
            return _component_from_refs(
                "character_state_delta",
                refs,
                availability="partial",
                granularity="task_final_snapshot",
                producer_status="final_snapshot_only",
            )
        return _unavailable(
            "character_state_delta",
            "no_persisted_subsection_character_state_delta",
        )

    @staticmethod
    def _relationship_component(
        task_hash: str,
        section: int,
        subsection: int,
        records: Sequence[Mapping[str, Any]],
        is_last: bool,
    ) -> OutcomeComponent:
        exact = [
            _mapping(item)
            for item in records
            if _positive_int(item.get("source_section") or item.get("section"))
            == section
            and _positive_int(item.get("subsection")) == subsection
            and item.get("before_state")
            and item.get("after_state")
        ]
        if exact:
            refs = [
                _source_ref(
                    item,
                    source_type="relationship",
                    source_id=_stable_item_id(
                        item,
                        prefix="relationship",
                        task_id_hash=task_hash,
                        index=index,
                    ),
                    producer="character_relation_store",
                    storage_location="character_relations.db",
                    section=section,
                    subsection=subsection,
                    granularity="subsection_exact",
                    authority="authoritative_store",
                    provenance="explicit_relationship_delta",
                )
                for index, item in enumerate(exact, 1)
            ]
            return _component_from_refs(
                "relationship_delta",
                refs,
                availability="available",
                granularity="subsection_exact",
                producer_status="completed",
            )
        if is_last and records:
            refs = [
                _source_ref(
                    _mapping(item),
                    source_type="relationship",
                    source_id=_stable_item_id(
                        _mapping(item),
                        prefix="relationship-current",
                        task_id_hash=task_hash,
                        index=index,
                    ),
                    producer="character_relation_store",
                    storage_location="character_relations.db",
                    section=_positive_int(item.get("source_section")),
                    subsection=None,
                    granularity="current_store_snapshot",
                    authority="authoritative_store",
                    provenance="current_snapshot_not_subsection_delta",
                )
                for index, item in enumerate(records, 1)
            ]
            return _component_from_refs(
                "relationship_delta",
                refs,
                availability="partial",
                granularity="current_store_snapshot",
                producer_status="current_snapshot_only",
            )
        return _unavailable(
            "relationship_delta", "no_persisted_relationship_records"
        )

    @staticmethod
    def _foreshadow_component(
        task_hash: str,
        section: int,
        subsection: int,
        records: Sequence[Mapping[str, Any]],
        is_last: bool,
    ) -> OutcomeComponent:
        exact = [
            _mapping(item)
            for item in records
            if _positive_int(item.get("section")) == section
            and _positive_int(item.get("subsection")) == subsection
            and item.get("before_status")
            and item.get("after_status")
        ]
        if exact:
            refs = [
                _source_ref(
                    item,
                    source_type="foreshadowing",
                    source_id=_stable_item_id(
                        item,
                        prefix="foreshadow",
                        task_id_hash=task_hash,
                        index=index,
                    ),
                    producer="foreshadowing_store",
                    storage_location="foreshadowings.db",
                    section=section,
                    subsection=subsection,
                    granularity="subsection_exact",
                    authority="authoritative_store",
                    provenance="explicit_lifecycle_delta",
                )
                for index, item in enumerate(exact, 1)
            ]
            return _component_from_refs(
                "foreshadow_delta",
                refs,
                availability="available",
                granularity="subsection_exact",
                producer_status="completed",
            )
        if is_last and records:
            refs = [
                _source_ref(
                    _mapping(item),
                    source_type="foreshadowing",
                    source_id=_stable_item_id(
                        _mapping(item),
                        prefix="foreshadow-current",
                        task_id_hash=task_hash,
                        index=index,
                    ),
                    producer="foreshadowing_store",
                    storage_location="foreshadowings.db",
                    section=_positive_int(item.get("plant_chapter")),
                    subsection=None,
                    granularity="current_store_snapshot",
                    authority="authoritative_store",
                    provenance="current_snapshot_not_lifecycle_delta",
                )
                for index, item in enumerate(records, 1)
            ]
            return _component_from_refs(
                "foreshadow_delta",
                refs,
                availability="partial",
                granularity="current_store_snapshot",
                producer_status="current_snapshot_only",
            )
        return _unavailable(
            "foreshadow_delta", "no_persisted_subsection_foreshadow_lifecycle"
        )

    @staticmethod
    def _experience_component(
        task_hash: str,
        section: int,
        subsection: int,
        records: Sequence[Mapping[str, Any]],
        is_last: bool,
    ) -> OutcomeComponent:
        exact = [
            _mapping(item)
            for item in records
            if _positive_int(item.get("chapter") or item.get("section")) == section
            and _positive_int(item.get("subsection")) == subsection
        ]
        if exact:
            refs = [
                _source_ref(
                    item,
                    source_type="experience",
                    source_id=_stable_item_id(
                        item,
                        prefix="experience",
                        task_id_hash=task_hash,
                        index=index,
                    ),
                    producer="experience_timeline.extract_from_section",
                    storage_location="events.db",
                    section=section,
                    subsection=subsection,
                    granularity="subsection_exact",
                    authority="authoritative_store",
                    provenance="explicit_event_subsection",
                )
                for index, item in enumerate(exact, 1)
            ]
            return _component_from_refs(
                "experience_delta",
                refs,
                availability="available",
                granularity="subsection_exact",
                producer_status="completed",
            )
        aggregate = [
            _mapping(item)
            for item in records
            if _positive_int(item.get("chapter") or item.get("section")) == section
            and _positive_int(item.get("subsection")) is None
        ]
        if is_last and aggregate:
            refs = [
                _source_ref(
                    item,
                    source_type="experience",
                    source_id=_stable_item_id(
                        item,
                        prefix="experience-section",
                        task_id_hash=task_hash,
                        index=index,
                    ),
                    producer="experience_timeline.extract_from_section",
                    storage_location="events.db",
                    section=section,
                    subsection=None,
                    granularity="section_aggregate",
                    authority="authoritative_store",
                    provenance="section_event_without_subsection_boundary",
                )
                for index, item in enumerate(aggregate, 1)
            ]
            return _component_from_refs(
                "experience_delta",
                refs,
                availability="partial",
                granularity="section_aggregate",
                producer_status="section_aggregate_only",
            )
        return _unavailable(
            "experience_delta", "no_persisted_subsection_experience_event"
        )
