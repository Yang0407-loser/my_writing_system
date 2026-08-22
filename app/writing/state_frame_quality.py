"""Conservative quality observations over StateFrame deltas."""

from __future__ import annotations

from .state_frame_v1 import (
    QualityMetric,
    StateDelta,
    StateFrameQualityObservation,
    StateFrameSnapshot,
)


ALLOWED_ATTRIBUTIONS = {
    "writer_omission",
    "writer_contradiction",
    "upstream_expectation_missing",
    "upstream_expectation_ambiguous",
    "extractor_possible_false_negative",
    "extractor_possible_false_positive",
    "state_commit_propagation_error",
    "stale_state_source",
    "conflicting_state_sources",
    "foreshadow_data_quality_error",
    "insufficient_evidence",
}


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _subjects_overlap(left: str, right: str) -> bool:
    left_values = {value for value in left.split("|") if value}
    right_values = {value for value in right.split("|") if value}
    return bool(left_values & right_values)


class StateFrameQualityEvaluator:
    """Never treats extractor output as independent truth."""

    def evaluate(
        self,
        before: StateFrameSnapshot,
        after: StateFrameSnapshot,
        delta: StateDelta,
    ) -> StateFrameQualityObservation:
        all_delta_facts = [
            *delta.added_facts,
            *(item.after for item in delta.changed_facts if item.after is not None),
        ]
        handover_expectations = [
            item for item in before.facts
            if item.fact_type in {"continuity_state", "open_event_chain"}
            and item.status == "pending"
        ]
        handover_assessable = [
            item for item in handover_expectations
            if item.source_id and item.source_hash
        ]
        handover_supported = [
            item for item in handover_assessable
            if any(
                fact.subject == item.subject and fact.predicate == item.predicate
                for fact in all_delta_facts
            )
        ]

        transition_expectations = [
            item for item in after.expectations
            if item.expectation_type in {"state_transition", "decision"}
        ]
        assessable_transitions = [
            item for item in transition_expectations
            if item.subject != "unassigned" and item.confidence >= 0.6
        ]
        state_changes = [
            fact for fact in all_delta_facts
            if fact.fact_type in {"character_state", "relationship_state"}
        ]
        supported_transitions = [
            item for item in assessable_transitions
            if any(
                _subjects_overlap(item.subject, fact.subject)
                for fact in state_changes
                if fact.subject
            )
        ]

        foreshadow_facts = [
            fact for fact in all_delta_facts if fact.fact_type == "foreshadow_state"
        ]
        planned_foreshadows = [
            item for item in after.expectations
            if item.expectation_type == "foreshadowing"
        ]
        traceable = [
            fact for fact in after.facts if fact.source_id and fact.source_hash
        ]
        traceability_rate = (
            len(traceable) / len(after.facts) if after.facts else 1.0
        )

        handover_unassessable = len(handover_expectations) - len(handover_assessable)
        transition_unassessable = (
            len(transition_expectations) - len(assessable_transitions)
        )
        metrics = (
            QualityMetric(
                dimension="handover_continuity",
                counts={
                    "handover_required_items": len(handover_expectations),
                    "handover_assessable_items": len(handover_assessable),
                    "handover_satisfied_items": len(handover_supported),
                    "handover_partial_items": 0,
                    "handover_contradictions": sum(
                        fact.status == "conflicted" for fact in all_delta_facts
                    ),
                    "handover_unassessable_items": handover_unassessable,
                    "handover_coverage": _ratio(
                        len(handover_supported), len(handover_assessable)
                    ),
                },
                evaluation_basis=(
                    "extractor_reported"
                    if handover_assessable else "insufficient_evidence"
                ),
                attributions=(
                    ("extractor_possible_false_negative",)
                    if handover_assessable and len(handover_supported) < len(handover_assessable)
                    else ()
                ),
                unavailable_reasons=(
                    ("unstructured_or_missing_handover_contract",)
                    if handover_unassessable else ()
                ),
            ),
            QualityMetric(
                dimension="character_state_transition",
                counts={
                    "expected_state_transitions": len(transition_expectations),
                    "assessable_state_transitions": len(assessable_transitions),
                    "supported_state_transitions": len(supported_transitions),
                    "partial_state_transitions": 0,
                    "contradicted_state_transitions": 0,
                    "unsupported_state_changes": len([
                        fact for fact in state_changes
                        if fact.provenance == "extractor_reported"
                        and not any(
                            _subjects_overlap(item.subject, fact.subject)
                            for item in transition_expectations
                        )
                    ]),
                    "missing_state_transitions": max(
                        0, len(assessable_transitions) - len(supported_transitions)
                    ),
                    "character_state_consistency": _ratio(
                        len(supported_transitions), len(assessable_transitions)
                    ),
                    "unassessable_state_transitions": transition_unassessable,
                },
                evaluation_basis=(
                    "extractor_reported"
                    if assessable_transitions else "insufficient_evidence"
                ),
                attributions=(
                    ("insufficient_evidence",) if transition_unassessable else ()
                ),
            ),
            QualityMetric(
                dimension="foreshadow_health",
                counts={
                    "planned_foreshadow_actions": len(planned_foreshadows),
                    "assessable_foreshadow_actions": 0,
                    "supported_foreshadow_actions": 0,
                    "newly_planted": sum(
                        isinstance(fact.value, dict)
                        and fact.value.get("status") in {"pending", "planted"}
                        for fact in foreshadow_facts
                    ),
                    "advanced": sum(
                        isinstance(fact.value, dict)
                        and fact.value.get("status") == "hinted"
                        for fact in foreshadow_facts
                    ),
                    "resolved": sum(
                        isinstance(fact.value, dict)
                        and fact.value.get("status") == "resolved"
                        for fact in foreshadow_facts
                    ),
                    "premature_resolutions": 0,
                    "overdue_foreshadows": 0,
                    "invalid_resolve_chapter_count": sum(
                        isinstance(fact.value, dict)
                        and fact.value.get("invalid_resolve_chapter") is True
                        for fact in after.facts
                        if fact.fact_type == "foreshadow_state"
                    ),
                    "foreshadow_lifecycle_consistency": None,
                },
                evaluation_basis="insufficient_evidence",
                unavailable_reasons=(
                    "current_store_has_no_subsection_lifecycle_history",
                ),
            ),
        )
        return StateFrameQualityObservation(
            task_id_hash=after.task_id_hash,
            section=after.section,
            subsection=after.subsection,
            before_frame_hash=before.frame_hash,
            after_frame_hash=after.frame_hash,
            delta_id=delta.delta_id,
            metrics=metrics,
            source_traceability_rate=traceability_rate,
        )
