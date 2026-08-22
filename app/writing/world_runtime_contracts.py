"""WR0-A contracts for a world runtime without production integration.

The models in this module deliberately keep candidate conventions, confirmed
project rules, canonical state, and narrative preferences in separate
artifacts.  They are data contracts only: importing this module does not read
or write storage, compile a runtime frame, or alter Writer behavior.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


WORLD_RUNTIME_CONTRACT_VERSION = "world-runtime-contracts-wr0a-v1"

RuleKind = Literal[
    "invariant",
    "precondition",
    "transition",
    "permission",
    "prohibition",
    "default_assumption",
]
RuleAuthority = Literal[
    "kernel",
    "project_explicit",
    "user_override",
    "pack_candidate",
    "model_inferred",
    "text_extracted",
]
Enforcement = Literal["block", "warn", "suggest", "inactive"]
ActivationEnforcement = Literal["block", "warn", "suggest"]
EpistemicStatus = Literal[
    "confirmed_true",
    "confirmed_false",
    "unknown",
    "proposed",
    "deprecated",
]
ValidationOutcome = Literal[
    "valid", "invalid", "unresolved", "valid_with_exception"
]
PredicateOperator = Literal[
    "equals",
    "not_equals",
    "in",
    "not_in",
    "exists",
    "not_exists",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
]
EffectOperation = Literal["set", "add", "remove", "increment", "decrement"]
NarrativeInfluence = Literal["suggest", "inactive"]


def canonical_hash(value: Any) -> str:
    """Return a stable hash for a JSON-compatible contract value."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class FrozenRuntimeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProvenanceRef(FrozenRuntimeModel):
    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    producer: str = Field(min_length=1)


class ValidTime(FrozenRuntimeModel):
    valid_from: str | None = None
    valid_until: str | None = None


class RuleScope(FrozenRuntimeModel):
    project_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    location_ids: tuple[str, ...] = ()
    lifecycle_ids: tuple[str, ...] = ()
    section: int | None = Field(default=None, ge=1)
    subsection: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def normalize_unique_scope_members(self):
        for field_name in ("entity_ids", "location_ids", "lifecycle_ids"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")
        if self.subsection is not None and self.section is None:
            raise ValueError("subsection scope requires section")
        return self


class StatePredicate(FrozenRuntimeModel):
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    operator: PredicateOperator
    expected: Any = None
    fact_id: str | None = None


class StateEffect(FrozenRuntimeModel):
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    operation: EffectOperation
    value: Any = None


class WorldRule(FrozenRuntimeModel):
    rule_id: str = Field(min_length=1)
    semantic_key: str = Field(min_length=1)
    kind: RuleKind
    authority: RuleAuthority
    enforcement: Enforcement
    activation_enforcement: ActivationEnforcement | None = None
    scope: RuleScope = Field(default_factory=RuleScope)
    conditions: tuple[StatePredicate, ...] = ()
    prerequisites: tuple[StatePredicate, ...] = ()
    effects: tuple[StateEffect, ...] = ()
    overrides_rule_ids: tuple[str, ...] = ()
    provenance: ProvenanceRef
    version: str = Field(min_length=1)
    valid_time: ValidTime = Field(default_factory=ValidTime)
    schema_version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @model_validator(mode="after")
    def enforce_authority_boundaries(self):
        if self.authority == "pack_candidate" and self.enforcement != "inactive":
            raise ValueError("pack candidate rules must remain inactive")
        if (
            self.authority == "pack_candidate"
            and self.activation_enforcement is None
        ):
            raise ValueError(
                "pack candidate rules require an activation enforcement recommendation"
            )
        if (
            self.authority != "pack_candidate"
            and self.activation_enforcement is not None
        ):
            raise ValueError(
                "only pack candidate rules may carry activation enforcement"
            )
        if (
            self.authority in {"model_inferred", "text_extracted"}
            and self.enforcement == "block"
        ):
            raise ValueError("inferred or extracted rules cannot block")
        if self.rule_id in self.overrides_rule_ids:
            raise ValueError("a rule cannot override itself")
        if len(self.overrides_rule_ids) != len(set(self.overrides_rule_ids)):
            raise ValueError("overrides_rule_ids must not contain duplicates")
        return self


class WorldFact(FrozenRuntimeModel):
    fact_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    value: Any = None
    epistemic_status: EpistemicStatus
    authority: RuleAuthority
    provenance: ProvenanceRef
    revision: int = Field(ge=0)
    valid_time: ValidTime = Field(default_factory=ValidTime)
    schema_version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @model_validator(mode="after")
    def preserve_unknown_semantics(self):
        if self.epistemic_status == "unknown" and self.value is not None:
            raise ValueError("unknown facts must not carry an assumed value")
        if self.epistemic_status == "proposed" and self.authority in {
            "kernel",
            "project_explicit",
            "user_override",
        }:
            raise ValueError("authoritative facts cannot remain proposed")
        return self


class LifecycleTransition(FrozenRuntimeModel):
    transition_id: str = Field(min_length=1)
    from_state: str = Field(min_length=1)
    to_state: str = Field(min_length=1)
    guards: tuple[StatePredicate, ...] = ()
    effects: tuple[StateEffect, ...] = ()
    reversible: bool = False


class Lifecycle(FrozenRuntimeModel):
    lifecycle_id: str = Field(min_length=1)
    semantic_key: str = Field(min_length=1)
    states: tuple[str, ...] = Field(min_length=2)
    initial_state: str = Field(min_length=1)
    transitions: tuple[LifecycleTransition, ...] = ()
    terminal_states: tuple[str, ...] = ()
    authority: RuleAuthority
    enforcement: Enforcement
    activation_enforcement: ActivationEnforcement | None = None
    scope: RuleScope = Field(default_factory=RuleScope)
    provenance: ProvenanceRef
    version: str = Field(min_length=1)
    valid_time: ValidTime = Field(default_factory=ValidTime)
    schema_version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @model_validator(mode="after")
    def validate_state_machine(self):
        state_set = set(self.states)
        if len(state_set) != len(self.states):
            raise ValueError("lifecycle states must be unique")
        if self.initial_state not in state_set:
            raise ValueError("initial_state must exist in states")
        if not set(self.terminal_states).issubset(state_set):
            raise ValueError("terminal_states must exist in states")
        transition_ids: set[str] = set()
        for transition in self.transitions:
            if transition.transition_id in transition_ids:
                raise ValueError("lifecycle transition IDs must be unique")
            transition_ids.add(transition.transition_id)
            if transition.from_state not in state_set or transition.to_state not in state_set:
                raise ValueError("lifecycle transitions must reference declared states")
            if transition.from_state == transition.to_state:
                raise ValueError("lifecycle transitions must change state")
        if self.authority == "pack_candidate" and self.enforcement != "inactive":
            raise ValueError("pack candidate lifecycles must remain inactive")
        if (
            self.authority == "pack_candidate"
            and self.activation_enforcement is None
        ):
            raise ValueError(
                "pack candidate lifecycles require an activation enforcement recommendation"
            )
        if (
            self.authority != "pack_candidate"
            and self.activation_enforcement is not None
        ):
            raise ValueError(
                "only pack candidate lifecycles may carry activation enforcement"
            )
        if (
            self.authority in {"model_inferred", "text_extracted"}
            and self.enforcement == "block"
        ):
            raise ValueError("inferred or extracted lifecycles cannot block")
        return self


class NarrativePreference(FrozenRuntimeModel):
    preference_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    authority: Literal[
        "project_explicit", "user_override", "pack_candidate", "model_inferred"
    ]
    influence: NarrativeInfluence
    scope: RuleScope = Field(default_factory=RuleScope)
    provenance: ProvenanceRef
    version: str = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @model_validator(mode="after")
    def keep_candidates_inactive(self):
        if self.authority == "pack_candidate" and self.influence != "inactive":
            raise ValueError("pack narrative preferences must remain inactive")
        return self


class CandidatePack(FrozenRuntimeModel):
    pack_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    rules: tuple[WorldRule, ...] = ()
    lifecycles: tuple[Lifecycle, ...] = ()
    narrative_preferences: tuple[NarrativePreference, ...] = ()
    schema_version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @model_validator(mode="after")
    def reject_active_or_authoritative_content(self):
        if any(item.authority != "pack_candidate" for item in self.rules):
            raise ValueError("candidate packs may contain only pack_candidate rules")
        if any(item.authority != "pack_candidate" for item in self.lifecycles):
            raise ValueError("candidate packs may contain only pack_candidate lifecycles")
        if any(
            item.authority != "pack_candidate"
            for item in self.narrative_preferences
        ):
            raise ValueError(
                "candidate packs may contain only pack_candidate preferences"
            )
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(
            {
                "pack_id": self.pack_id,
                "version": self.version,
                "rules": sorted(
                    self.rules,
                    key=lambda item: (item.semantic_key, item.rule_id, canonical_hash(item)),
                ),
                "lifecycles": sorted(
                    self.lifecycles,
                    key=lambda item: (
                        item.semantic_key,
                        item.lifecycle_id,
                        canonical_hash(item),
                    ),
                ),
                "narrative_preferences": sorted(
                    self.narrative_preferences,
                    key=lambda item: (
                        item.preference_id,
                        canonical_hash(item),
                    ),
                ),
                "schema_version": self.schema_version,
            }
        )


class ProjectWorldConstitution(FrozenRuntimeModel):
    project_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    rules: tuple[WorldRule, ...] = ()
    lifecycles: tuple[Lifecycle, ...] = ()
    bound_candidate_packs: tuple[str, ...] = ()
    schema_version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @model_validator(mode="after")
    def reject_candidates_and_current_state(self):
        allowed = {"kernel", "project_explicit", "user_override"}
        if any(item.authority not in allowed for item in self.rules):
            raise ValueError("constitution rules must be explicit or kernel rules")
        if any(item.authority not in allowed for item in self.lifecycles):
            raise ValueError(
                "constitution lifecycles must be explicit or kernel definitions"
            )
        if len(self.bound_candidate_packs) != len(set(self.bound_candidate_packs)):
            raise ValueError("bound_candidate_packs must not contain duplicates")
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class CanonicalWorldState(FrozenRuntimeModel):
    project_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    facts: tuple[WorldFact, ...] = ()
    schema_version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @model_validator(mode="after")
    def reject_candidates_and_future_revisions(self):
        fact_ids: set[str] = set()
        for fact in self.facts:
            if fact.fact_id in fact_ids:
                raise ValueError("canonical fact IDs must be unique")
            fact_ids.add(fact.fact_id)
            if fact.epistemic_status == "proposed":
                raise ValueError("proposed facts cannot enter canonical state")
            if fact.authority == "pack_candidate":
                raise ValueError("pack candidates cannot enter canonical state")
            if fact.revision > self.revision:
                raise ValueError("fact revision cannot exceed state revision")
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class NarrativePolicy(FrozenRuntimeModel):
    project_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    preferences: tuple[NarrativePreference, ...] = ()
    schema_version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class TransitionValidation(FrozenRuntimeModel):
    validation_id: str = Field(min_length=1)
    outcome: ValidationOutcome
    rule_ids: tuple[str, ...] = ()
    unresolved_fact_ids: tuple[str, ...] = ()
    exception_rule_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    schema_version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @model_validator(mode="after")
    def require_outcome_evidence(self):
        if self.outcome == "unresolved" and not self.unresolved_fact_ids:
            raise ValueError("unresolved validation requires unresolved facts")
        if self.outcome == "valid_with_exception" and not self.exception_rule_ids:
            raise ValueError("valid_with_exception requires an exception rule")
        return self
