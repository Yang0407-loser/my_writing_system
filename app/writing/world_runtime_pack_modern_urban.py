"""WR0-D thin, inactive candidate pack for a 2020s Chinese modern city.

This module contains reusable candidates, not confirmed world truth.  Loading
or resolving the pack cannot activate any rule or lifecycle.  Project-specific
facts such as a shop's name or opening days belong in the project constitution
and canonical state instead.
"""

from __future__ import annotations

from .world_runtime_contracts import (
    CandidatePack,
    Lifecycle,
    LifecycleTransition,
    ProvenanceRef,
    StateEffect,
    StatePredicate,
    WorldRule,
    canonical_hash,
)


MODERN_URBAN_CN_2020S_PACK_ID = "modern-urban-cn-2020s"
MODERN_URBAN_CN_2020S_PACK_VERSION = "1.0.0"
MODERN_URBAN_CN_2020S_PACK_REF = (
    f"{MODERN_URBAN_CN_2020S_PACK_ID}@{MODERN_URBAN_CN_2020S_PACK_VERSION}"
)

MODERN_URBAN_CN_2020S_RULE_IDS = (
    "modern-urban.calendar.seven-day-weekday-cycle",
    "modern-urban.employment.termination-requires-effective-resignation",
    "modern-urban.publication.public-reaction-requires-reach",
    "modern-urban.publication.public-visibility-requires-publication",
    "modern-urban.resignation.private-draft-is-not-delivery",
    "modern-urban.storefront.internal-activity-is-not-public-opening",
    "modern-urban.storefront.public-opening-requires-schedule-or-exception",
)

MODERN_URBAN_CN_2020S_LIFECYCLE_IDS = (
    "modern-urban.lifecycle.knowledge-transmission",
    "modern-urban.lifecycle.publication",
    "modern-urban.lifecycle.resignation",
    "modern-urban.lifecycle.storefront-operation",
)


def _provenance(artifact_id: str, semantic_key: str) -> ProvenanceRef:
    source = {
        "pack_id": MODERN_URBAN_CN_2020S_PACK_ID,
        "version": MODERN_URBAN_CN_2020S_PACK_VERSION,
        "artifact_id": artifact_id,
        "semantic_key": semantic_key,
    }
    return ProvenanceRef(
        source_id=f"{MODERN_URBAN_CN_2020S_PACK_REF}:{artifact_id}",
        source_type="candidate_pack_definition",
        source_hash=canonical_hash(source),
        producer="build_modern_urban_cn_2020s_candidate_pack",
    )


def _required_true(subject: str, predicate: str) -> tuple[StatePredicate, ...]:
    return (
        StatePredicate(
            subject=subject,
            predicate=predicate,
            operator="equals",
            expected=True,
        ),
    )


def _candidate_rule(
    *,
    rule_id: str,
    semantic_key: str,
    kind: str,
    activation_enforcement: str,
    subject: str,
    predicate: str,
) -> WorldRule:
    return WorldRule(
        rule_id=rule_id,
        semantic_key=semantic_key,
        kind=kind,
        authority="pack_candidate",
        enforcement="inactive",
        activation_enforcement=activation_enforcement,
        prerequisites=_required_true(subject, predicate),
        provenance=_provenance(rule_id, semantic_key),
        version=MODERN_URBAN_CN_2020S_PACK_VERSION,
    )


def _transition(
    lifecycle_id: str,
    name: str,
    from_state: str,
    to_state: str,
    *,
    guard: str | None = None,
) -> LifecycleTransition:
    guards = _required_true(f"${lifecycle_id}", guard) if guard else ()
    return LifecycleTransition(
        transition_id=f"{lifecycle_id}.{name}",
        from_state=from_state,
        to_state=to_state,
        guards=guards,
        effects=(
            StateEffect(
                subject=f"${lifecycle_id}",
                predicate="state",
                operation="set",
                value=to_state,
            ),
        ),
    )


def _candidate_lifecycle(
    *,
    lifecycle_id: str,
    semantic_key: str,
    states: tuple[str, ...],
    transitions: tuple[LifecycleTransition, ...],
    terminal_states: tuple[str, ...] = (),
    activation_enforcement: str = "block",
) -> Lifecycle:
    return Lifecycle(
        lifecycle_id=lifecycle_id,
        semantic_key=semantic_key,
        states=states,
        initial_state=states[0],
        transitions=transitions,
        terminal_states=terminal_states,
        authority="pack_candidate",
        enforcement="inactive",
        activation_enforcement=activation_enforcement,
        provenance=_provenance(lifecycle_id, semantic_key),
        version=MODERN_URBAN_CN_2020S_PACK_VERSION,
    )


def build_modern_urban_cn_2020s_candidate_pack() -> CandidatePack:
    """Return the frozen WR0-D candidate pack; every entry is inactive."""

    rules = (
        _candidate_rule(
            rule_id="modern-urban.calendar.seven-day-weekday-cycle",
            semantic_key="calendar.weekday_cycle.seven_day",
            kind="default_assumption",
            activation_enforcement="suggest",
            subject="$calendar",
            predicate="uses_seven_day_weekday_cycle",
        ),
        _candidate_rule(
            rule_id="modern-urban.employment.termination-requires-effective-resignation",
            semantic_key="employment.termination.prerequisite",
            kind="precondition",
            activation_enforcement="block",
            subject="$employment_termination",
            predicate="resignation_or_termination_is_effective",
        ),
        _candidate_rule(
            rule_id="modern-urban.publication.public-reaction-requires-reach",
            semantic_key="publication.public_reaction.reach",
            kind="precondition",
            activation_enforcement="block",
            subject="$public_reaction",
            predicate="content_has_reached_reacting_actor",
        ),
        _candidate_rule(
            rule_id="modern-urban.publication.public-visibility-requires-publication",
            semantic_key="publication.public_visibility.prerequisite",
            kind="precondition",
            activation_enforcement="block",
            subject="$public_visibility",
            predicate="content_is_published",
        ),
        _candidate_rule(
            rule_id="modern-urban.resignation.private-draft-is-not-delivery",
            semantic_key="employment.resignation.private_draft_delivery",
            kind="invariant",
            activation_enforcement="block",
            subject="$resignation_delivery",
            predicate="has_institutional_delivery_path",
        ),
        _candidate_rule(
            rule_id="modern-urban.storefront.internal-activity-is-not-public-opening",
            semantic_key="storefront.internal_activity.public_opening",
            kind="invariant",
            activation_enforcement="warn",
            subject="$storefront_public_opening",
            predicate="is_distinct_from_internal_activity",
        ),
        _candidate_rule(
            rule_id="modern-urban.storefront.public-opening-requires-schedule-or-exception",
            semantic_key="storefront.public_opening.schedule",
            kind="precondition",
            activation_enforcement="block",
            subject="$storefront_public_opening",
            predicate="scheduled_open_or_explicit_exception_exists",
        ),
    )

    storefront_id = "modern-urban.lifecycle.storefront-operation"
    publication_id = "modern-urban.lifecycle.publication"
    knowledge_id = "modern-urban.lifecycle.knowledge-transmission"
    resignation_id = "modern-urban.lifecycle.resignation"
    lifecycles = (
        _candidate_lifecycle(
            lifecycle_id=knowledge_id,
            semantic_key="communication.knowledge_transmission.lifecycle",
            states=("unknown", "available", "reached", "perceived", "understood"),
            transitions=(
                _transition(knowledge_id, "make-available", "unknown", "available", guard="source_exposes_information"),
                _transition(knowledge_id, "reach", "available", "reached", guard="delivery_path_exists"),
                _transition(knowledge_id, "perceive", "reached", "perceived", guard="actor_perceives_information"),
                _transition(knowledge_id, "understand", "perceived", "understood", guard="actor_understands_information"),
            ),
            terminal_states=("understood",),
        ),
        _candidate_lifecycle(
            lifecycle_id=publication_id,
            semantic_key="publication.content.lifecycle",
            states=("draft", "submitted", "published", "distributed"),
            transitions=(
                _transition(publication_id, "submit", "draft", "submitted", guard="submission_occurs"),
                _transition(publication_id, "publish", "submitted", "published", guard="publication_occurs"),
                _transition(publication_id, "distribute", "published", "distributed", guard="distribution_occurs"),
            ),
            terminal_states=("distributed",),
        ),
        _candidate_lifecycle(
            lifecycle_id=resignation_id,
            semantic_key="employment.resignation.lifecycle",
            states=("private_draft", "delivered", "acknowledged", "notice_period", "terminated"),
            transitions=(
                _transition(resignation_id, "deliver", "private_draft", "delivered", guard="institutional_delivery_occurs"),
                _transition(resignation_id, "acknowledge", "delivered", "acknowledged", guard="employer_acknowledges_notice"),
                _transition(resignation_id, "begin-notice-period", "acknowledged", "notice_period", guard="notice_period_begins"),
                _transition(resignation_id, "terminate", "notice_period", "terminated", guard="termination_becomes_effective"),
            ),
            terminal_states=("terminated",),
        ),
        _candidate_lifecycle(
            lifecycle_id=storefront_id,
            semantic_key="storefront.operation.lifecycle",
            states=("closed", "internal_activity", "open_to_public"),
            transitions=(
                _transition(storefront_id, "start-internal-activity", "closed", "internal_activity", guard="authorized_actor_enters"),
                _transition(storefront_id, "open-to-public", "internal_activity", "open_to_public", guard="schedule_or_exception_allows_opening"),
                _transition(storefront_id, "close-after-internal-activity", "internal_activity", "closed", guard="internal_activity_ends"),
                _transition(storefront_id, "close-to-public", "open_to_public", "closed", guard="public_opening_ends"),
            ),
        ),
    )

    return CandidatePack(
        pack_id=MODERN_URBAN_CN_2020S_PACK_ID,
        version=MODERN_URBAN_CN_2020S_PACK_VERSION,
        rules=tuple(sorted(rules, key=lambda item: item.rule_id)),
        lifecycles=tuple(sorted(lifecycles, key=lambda item: item.lifecycle_id)),
    )

