"""Pure Legacy Candidate to complete canonical state snapshot compiler."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import (
    CanonicalStateSnapshot,
    StateTransitionResult,
    SubsectionCandidate,
)
from .errors import StateVersionConflict


class LegacyStateTransitionAdapter:
    """Maintain only the opaque `foundation_state_v0` envelope."""

    transition_version = "legacy-foundation-state-transition-v0"

    def compile(
        self,
        *,
        base_state: CanonicalStateSnapshot,
        candidate: SubsectionCandidate,
    ) -> StateTransitionResult:
        if base_state.version_id != candidate.base_state_version_id:
            raise StateVersionConflict(
                "candidate base_state_version_id does not match loaded Project State Head"
            )

        next_state = deepcopy(base_state.state_json)
        existing_envelope = next_state.get("foundation_state_v0")
        envelope: dict[str, Any] = (
            deepcopy(existing_envelope) if isinstance(existing_envelope, dict) else {}
        )

        mutations_by_id: dict[str, dict[str, Any]] = {}
        for raw in envelope.get("world_mutations", []):
            if isinstance(raw, dict) and raw.get("mutation_id"):
                mutations_by_id[str(raw["mutation_id"])] = deepcopy(raw)
        for mutation in candidate.world_mutations:
            serialized = mutation.model_dump(mode="json")
            mutations_by_id[mutation.mutation_id] = serialized
        envelope["world_mutations"] = [
            mutations_by_id[mutation_id]
            for mutation_id in sorted(mutations_by_id)
        ]

        events_by_id: dict[str, dict[str, Any]] = {}
        for raw in envelope.get("ledger_events", []):
            if isinstance(raw, dict) and raw.get("event_id"):
                events_by_id[str(raw["event_id"])] = deepcopy(raw)
        for event in candidate.events:
            events_by_id.setdefault(
                event.event_id, event.model_dump(mode="json")
            )
        envelope["ledger_events"] = []
        for ordinal, event_id in enumerate(sorted(events_by_id), start=1):
            serialized = deepcopy(events_by_id[event_id])
            serialized["ordinal"] = ordinal
            envelope["ledger_events"].append(serialized)

        envelope["source_candidate_hash"] = candidate.candidate_hash
        next_state["foundation_state_v0"] = envelope
        return StateTransitionResult.create(
            transition_version=self.transition_version,
            candidate_hash=candidate.candidate_hash,
            base_state_version_id=base_state.version_id,
            next_state_json=next_state,
            ledger_events=candidate.events,
        )
