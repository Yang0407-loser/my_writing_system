"""World Runtime State Committer (canary C0, offline).

The single write boundary of the World Runtime: it atomically projects the
Validator-accepted typed delta into the next canonical state revision, an
Event Ledger and a StateFrame After.  Rejected/unresolved changes never enter
the commit; every commit advances exactly one revision; replay with the same
idempotency key is a no-op.

Canary C0 is offline-only: no provider calls, no production wiring, no
persistent writes outside the caller's own canary storage.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import SourceEvidence, StateAssertion, StateFrame, StoryStateSnapshot
from .state_frame import StateFrameCompiler
from .world_runtime_relationship_projection import RELATIONSHIP_PREDICATES
from .world_runtime_contracts import (
    CanonicalWorldState,
    FrozenRuntimeModel,
    ProvenanceRef,
    WorldFact,
    canonical_hash,
)


CONTRACT_VERSION = "world-runtime-state-committer-c0-v1"
ValidationOutcome = Literal["valid", "invalid", "unresolved"]
EpistemicInput = Literal["confirmed_true", "unknown"]

EVENT_ONLY_CHANGE_TYPES = frozenset({"storefront_public_sale", "storefront_public_handoff"})

CREATABLE_SLOTS: dict[str, tuple[frozenset[str], str]] = {
    "knowledge_state": (frozenset({"article_knowledge"}), "character:"),
    "location_state": (frozenset({"location"}), "character:"),
    "object_state": (frozenset({"content_state", "temperature_state", "location_state"}), "object:"),
    "resignation_personal_record": (frozenset({"personal_record_state"}), "resignation:"),
    "resignation_acknowledgement": (frozenset({"resignation_acknowledged"}), "company:"),
    "resignation_delivery": (frozenset({"lifecycle_state"}), "resignation:"),
    "publication_state": (frozenset({"publication_state"}), "article:"),
    "employment_state": (frozenset({"status"}), "employment:"),
    "clock_state": (frozenset({"time"}), "world_clock"),
    "relationship_state": (frozenset(RELATIONSHIP_PREDICATES), "relationship:"),
}


class CommittableChange(FrozenRuntimeModel):
    change_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    change_type: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    before_value: Any = None
    before_epistemic_status: EpistemicInput
    after_value: Any = None
    actor: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    schema_version: str = CONTRACT_VERSION


class CommittableDelta(FrozenRuntimeModel):
    delta_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    output_hash: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(default=())
    changes: tuple[CommittableChange, ...]
    schema_version: str = CONTRACT_VERSION

    @model_validator(mode="after")
    def ordered_unique_changes(self):
        ids = [change.change_id for change in self.changes]
        sequences = [change.sequence for change in self.changes]
        if len(ids) != len(set(ids)):
            raise ValueError("committable change IDs must be unique")
        if sequences != sorted(sequences) or sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("committable changes must be ordered by contiguous sequence")
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class CommittableValidationItem(FrozenRuntimeModel):
    change_id: str = Field(min_length=1)
    outcome: ValidationOutcome
    rule_ids: tuple[str, ...] = Field(default=())
    evidence_ids: tuple[str, ...] = Field(default=())
    schema_version: str = CONTRACT_VERSION


class CommittableValidation(FrozenRuntimeModel):
    validation_id: str = Field(min_length=1)
    delta_id: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    output_hash: str = Field(min_length=1)
    items: tuple[CommittableValidationItem, ...] = Field(default=())
    accepted_change_ids: tuple[str, ...] = Field(default=())
    rejected_change_ids: tuple[str, ...] = Field(default=())
    unresolved_change_ids: tuple[str, ...] = Field(default=())
    schema_version: str = CONTRACT_VERSION

    @model_validator(mode="after")
    def consistent_partitions(self):
        item_by_id = {item.change_id: item for item in self.items}
        actual = {
            "valid": {item.change_id for item in self.items if item.outcome == "valid"},
            "invalid": {item.change_id for item in self.items if item.outcome == "invalid"},
            "unresolved": {item.change_id for item in self.items if item.outcome == "unresolved"},
        }
        expected = {
            "valid": set(self.accepted_change_ids),
            "invalid": set(self.rejected_change_ids),
            "unresolved": set(self.unresolved_change_ids),
        }
        if actual != expected:
            raise ValueError("validation partition mismatch")
        if any(
            expected[left] & expected[right]
            for left, right in (("valid", "invalid"), ("valid", "unresolved"), ("invalid", "unresolved"))
        ):
            raise ValueError("validation partitions must not overlap")
        for change_id, item in item_by_id.items():
            if item.outcome == "invalid" and not item.rule_ids:
                raise ValueError("invalid transition requires rule IDs")
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class EventLedgerEntry(FrozenRuntimeModel):
    ledger_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    change_id: str = Field(min_length=1)
    change_type: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    after_value: Any = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    validation_outcome: ValidationOutcome = "valid"
    rule_ids: tuple[str, ...] = Field(default=())
    output_hash: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    fact_id: str | None = None
    schema_version: str = CONTRACT_VERSION


class EventLedger(FrozenRuntimeModel):
    ledger_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    entries: tuple[EventLedgerEntry, ...] = Field(default=())
    schema_version: str = CONTRACT_VERSION

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class CommittedWorldState(FrozenRuntimeModel):
    commit_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    before: CanonicalWorldState
    after: CanonicalWorldState
    ledger: EventLedger
    state_frame: StateFrame
    output_hash: str = Field(min_length=1)
    skipped_as_duplicate: bool = False
    schema_version: str = CONTRACT_VERSION

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class WorldRuntimeStateCommitter:
    """Idempotent, offline-only committer for accepted typed deltas."""

    PRODUCER = "world_runtime_state_committer"

    def __init__(self) -> None:
        self._committed: dict[str, CommittedWorldState] = {}

    @staticmethod
    def _provenance(source_id: str, payload: Any) -> ProvenanceRef:
        return ProvenanceRef(
            source_id=source_id,
            source_type="accepted_state_delta",
            source_hash=canonical_hash(payload),
            producer=WorldRuntimeStateCommitter.PRODUCER,
        )

    @staticmethod
    def _resolve_fact(facts: dict[str, Any], change: CommittableChange) -> Any:
        candidates = [
            fact
            for fact in facts.values()
            if fact.subject == change.subject and fact.predicate == change.predicate
        ]
        if len(candidates) > 1:
            raise ValueError(
                f"change {change.change_id} resolves to multiple facts "
                f"({change.subject}/{change.predicate}), got {len(candidates)}"
            )
        return candidates[0] if candidates else None

    @staticmethod
    def _derive_fact_id(subject: str, predicate: str) -> str:
        return f"fact:{subject.replace(':', '-')}:{predicate}"

    def _create_fact(
        self,
        *,
        change: CommittableChange,
        revision: int,
        commit_id: str,
        facts: dict[str, Any],
        created_ids: set[str],
    ) -> Any:
        spec = CREATABLE_SLOTS.get(change.change_type)
        if spec is None or change.predicate not in spec[0]:
            raise ValueError(
                f"change {change.change_id} cannot create fact: "
                f"({change.change_type}, {change.predicate}) not in creatable whitelist"
            )
        if not change.subject.startswith(spec[1]):
            raise ValueError(
                f"change {change.change_id} subject {change.subject} does not match "
                f"creatable prefix {spec[1]}"
            )
        if change.before_epistemic_status == "confirmed_true" and change.before_value not in (None, "unknown"):
            raise ValueError(
                f"change {change.change_id} claims an existing confirmed fact "
                f"but no slot exists"
            )
        fact_id = self._derive_fact_id(change.subject, change.predicate)
        if fact_id in facts or fact_id in created_ids:
            raise ValueError(f"change {change.change_id} duplicates created fact {fact_id}")
        fact = WorldFact(
            fact_id=fact_id,
            subject=change.subject,
            predicate=change.predicate,
            value=change.after_value,
            epistemic_status="confirmed_true",
            authority="text_extracted",
            provenance=self._provenance(
                f"{commit_id}:{change.change_id}",
                {"change": change.model_dump(), "revision": revision},
            ),
            revision=revision,
        )
        facts[fact_id] = fact
        created_ids.add(fact_id)
        return fact

    def _validate(
        self,
        *,
        before: CanonicalWorldState,
        delta: CommittableDelta,
        validation: CommittableValidation,
        final_text_hash: str,
    ) -> None:
        if delta.project_id != before.project_id:
            raise ValueError("delta project does not match canonical state project")
        if not (delta.base_revision == validation.base_revision == before.revision):
            raise ValueError("base revision mismatch across delta/validation/state")
        if not (delta.output_hash == validation.output_hash == final_text_hash):
            raise ValueError("output hash mismatch across delta/validation/final text")
        delta_change_ids = {change.change_id for change in delta.changes}
        accepted = set(validation.accepted_change_ids)
        if not accepted:
            raise ValueError("commit requires at least one accepted change")
        if not accepted.issubset(delta_change_ids):
            raise ValueError("accepted changes reference unknown delta changes")
        if set(validation.rejected_change_ids) & accepted:
            raise ValueError("rejected changes cannot enter commit")
        if set(validation.unresolved_change_ids) & accepted:
            raise ValueError("unresolved changes cannot enter commit")
        known_evidence = set(delta.evidence_ids)
        for change in delta.changes:
            if change.change_id in accepted and not set(change.evidence_ids).issubset(known_evidence):
                raise ValueError(f"accepted change {change.change_id} references unknown evidence")
        item_by_id = {item.change_id: item for item in validation.items}
        for change_id in accepted:
            item = item_by_id.get(change_id)
            if item is None or item.outcome != "valid":
                raise ValueError(f"accepted change {change_id} lacks a valid validation item")

    @staticmethod
    def _compile_state_frame(
        *,
        after: CanonicalWorldState,
        delta: CommittableDelta,
        validation: CommittableValidation,
        final_text_hash: str,
        task_id: str,
        section: int,
        subsection: int,
    ) -> StateFrame:
        accepted = set(validation.accepted_change_ids)
        ordered = [change for change in sorted(delta.changes, key=lambda item: item.sequence) if change.change_id in accepted]
        evidence: list[SourceEvidence] = []
        evidence_ids = set(delta.evidence_ids)
        for evidence_id in sorted(evidence_ids):
            evidence.append(SourceEvidence(
                evidence_id=evidence_id,
                source_id=evidence_id,
                source_type="final_text",
                text_hash=final_text_hash,
                excerpt="",
            ))
        assertions = [
            StateAssertion(
                assertion_id=f"assert:{change.change_id}",
                subject=change.subject,
                predicate=change.predicate,
                value=json.dumps(change.after_value, ensure_ascii=False)
                if not isinstance(change.after_value, str)
                else change.after_value,
                status="confirmed",
                evidence_ids=list(change.evidence_ids),
            )
            for change in ordered
        ]
        snapshot = StoryStateSnapshot(
            task_id=task_id,
            section=section,
            subsection=subsection,
            evidence=evidence,
            assertions=assertions,
            source_hash=final_text_hash,
        )
        return StateFrameCompiler().compile(snapshot)

    def commit(
        self,
        *,
        idempotency_key: str,
        before: CanonicalWorldState,
        delta: CommittableDelta,
        validation: CommittableValidation,
        final_text_hash: str,
        task_id: str = "saturday-bakery-canary",
        section: int = 1,
        subsection: int = 1,
    ) -> CommittedWorldState:
        if idempotency_key in self._committed:
            return self._committed[idempotency_key].model_copy(
                update={"skipped_as_duplicate": True}
            )
        self._validate(
            before=before,
            delta=delta,
            validation=validation,
            final_text_hash=final_text_hash,
        )
        revision = before.revision + 1
        commit_id = f"commit:{before.project_id}:canary:r{revision}"
        accepted_by_id = {
            change.change_id: change
            for change in delta.changes
            if change.change_id in validation.accepted_change_ids
        }
        ordered = sorted(accepted_by_id.values(), key=lambda change: change.sequence)
        facts = {fact.fact_id: fact for fact in before.facts}
        created_ids: set[str] = set()
        fact_by_change: dict[str, str | None] = {}
        for change in ordered:
            if change.change_type in EVENT_ONLY_CHANGE_TYPES:
                fact_by_change[change.change_id] = None
                continue
            fact = self._resolve_fact(facts, change)
            if fact is None:
                fact = self._create_fact(
                    change=change,
                    revision=revision,
                    commit_id=commit_id,
                    facts=facts,
                    created_ids=created_ids,
                )
                fact_by_change[change.change_id] = fact.fact_id
                continue
            if fact.value != change.before_value:
                raise ValueError(f"change {change.change_id} before value mismatch")
            if fact.epistemic_status != change.before_epistemic_status:
                raise ValueError(f"change {change.change_id} before status mismatch")
            facts[fact.fact_id] = fact.model_copy(
                update={
                    "value": change.after_value,
                    "epistemic_status": "confirmed_true",
                    "revision": revision,
                    "provenance": self._provenance(
                        f"{commit_id}:{change.change_id}",
                        {"change": change.model_dump(), "revision": revision},
                    ),
                }
            )
            fact_by_change[change.change_id] = fact.fact_id
        after = CanonicalWorldState(
            project_id=before.project_id,
            revision=revision,
            facts=tuple(sorted(facts.values(), key=lambda item: item.fact_id)),
        )
        item_by_id = {item.change_id: item for item in validation.items}
        entries = tuple(
            EventLedgerEntry(
                ledger_id=f"ledger:{before.project_id}:r{revision}",
                revision=revision,
                change_id=change.change_id,
                change_type=change.change_type,
                subject=change.subject,
                predicate=change.predicate,
                after_value=change.after_value,
                evidence_ids=change.evidence_ids,
                validation_outcome="valid",
                rule_ids=item_by_id[change.change_id].rule_ids,
                output_hash=final_text_hash,
                idempotency_key=idempotency_key,
                fact_id=fact_by_change.get(change.change_id),
            )
            for change in ordered
        )
        ledger = EventLedger(
            ledger_id=f"ledger:{before.project_id}:r{revision}",
            project_id=before.project_id,
            revision=revision,
            entries=entries,
        )
        state_frame = self._compile_state_frame(
            after=after,
            delta=delta,
            validation=validation,
            final_text_hash=final_text_hash,
            task_id=task_id,
            section=section,
            subsection=subsection,
        )
        result = CommittedWorldState(
            commit_id=commit_id,
            idempotency_key=idempotency_key,
            before=before,
            after=after,
            ledger=ledger,
            state_frame=state_frame,
            output_hash=final_text_hash,
        )
        self._committed[idempotency_key] = result
        return result
