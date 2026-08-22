"""WR0-A ownership and consumption contracts for world-runtime artifacts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .world_runtime_contracts import (
    FrozenRuntimeModel,
    WORLD_RUNTIME_CONTRACT_VERSION,
    canonical_hash,
)


ConsumptionStage = Literal[
    "world_mapping",
    "confirmation",
    "runtime_compile",
    "prompt_render",
    "transition_validate",
    "state_commit",
    "checkpoint",
    "review",
    "debug_api",
]
Retention = Literal["transient", "task", "project_versioned", "permanent_audit"]
ReceiptOutcome = Literal["consumed", "skipped", "fallback", "rejected"]


class ConsumerBinding(FrozenRuntimeModel):
    consumer: str = Field(min_length=1)
    stage: ConsumptionStage
    fields: tuple[str, ...] = Field(min_length=1)
    required: bool = True
    fallback: str = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_fields(self):
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("consumer fields must not contain duplicates")
        return self


class ArtifactConsumerContract(FrozenRuntimeModel):
    artifact_type: str = Field(min_length=1)
    producer: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    owns_semantics: bool
    semantic_keys: tuple[str, ...] = Field(min_length=1)
    stable_fields: tuple[str, ...] = Field(min_length=1)
    consumers: tuple[ConsumerBinding, ...] = Field(min_length=1)
    retention: Retention
    version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @model_validator(mode="after")
    def require_complete_field_consumption(self):
        if len(self.stable_fields) != len(set(self.stable_fields)):
            raise ValueError("stable_fields must not contain duplicates")
        if len(self.semantic_keys) != len(set(self.semantic_keys)):
            raise ValueError("semantic_keys must not contain duplicates")
        covered = {
            field_name
            for consumer in self.consumers
            for field_name in consumer.fields
        }
        unknown = covered - set(self.stable_fields)
        if unknown:
            raise ValueError(f"consumer references unknown stable fields: {sorted(unknown)}")
        orphaned = set(self.stable_fields) - covered
        if orphaned:
            raise ValueError(f"orphaned stable fields: {sorted(orphaned)}")
        return self


class RuntimeArtifactConsumerRegistry(FrozenRuntimeModel):
    contracts: tuple[ArtifactConsumerContract, ...] = Field(min_length=1)
    version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @model_validator(mode="after")
    def reject_duplicate_artifacts_and_authorities(self):
        artifact_types = [item.artifact_type for item in self.contracts]
        if len(artifact_types) != len(set(artifact_types)):
            raise ValueError("artifact_type must be unique")
        owners: dict[str, str] = {}
        for contract in self.contracts:
            if not contract.owns_semantics:
                continue
            for semantic_key in contract.semantic_keys:
                prior = owners.get(semantic_key)
                if prior is not None:
                    raise ValueError(
                        f"semantic authority conflict for {semantic_key}: "
                        f"{prior} and {contract.artifact_type}"
                    )
                owners[semantic_key] = contract.artifact_type
        return self

    @property
    def registry_hash(self) -> str:
        return canonical_hash(self)

    @property
    def orphaned_stable_fields(self) -> tuple[str, ...]:
        # Construction rejects orphaned fields; this property is an explicit
        # metric surface for reports and future consumption receipts.
        return ()


class ConsumptionReceipt(FrozenRuntimeModel):
    artifact_type: str = Field(min_length=1)
    artifact_hash: str = Field(min_length=1)
    consumer: str = Field(min_length=1)
    stage: ConsumptionStage
    consumed_fields: tuple[str, ...] = ()
    consumed_rule_ids: tuple[str, ...] = ()
    outcome: ReceiptOutcome
    fallback_code: str | None = None
    schema_version: str = WORLD_RUNTIME_CONTRACT_VERSION

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.outcome == "consumed" and not self.consumed_fields:
            raise ValueError("consumed receipts require consumed_fields")
        if self.outcome == "fallback" and not self.fallback_code:
            raise ValueError("fallback receipts require fallback_code")
        if self.outcome != "fallback" and self.fallback_code is not None:
            raise ValueError("fallback_code is only valid for fallback receipts")
        return self

    @property
    def receipt_hash(self) -> str:
        return canonical_hash(self)


def build_wr0a_consumer_registry() -> RuntimeArtifactConsumerRegistry:
    """Return the frozen WR0-A ownership map; no production wiring occurs."""

    return RuntimeArtifactConsumerRegistry(
        contracts=(
            ArtifactConsumerContract(
                artifact_type="CandidatePack",
                producer="candidate_pack_repository",
                authority="non_authoritative_inactive_candidates",
                owns_semantics=False,
                semantic_keys=("candidate_dimensions", "candidate_templates"),
                stable_fields=(
                    "pack_id",
                    "version",
                    "rules",
                    "lifecycles",
                    "narrative_preferences",
                    "schema_version",
                ),
                consumers=(
                    ConsumerBinding(
                        consumer="world_mapping",
                        stage="world_mapping",
                        fields=(
                            "pack_id",
                            "version",
                            "rules",
                            "lifecycles",
                            "narrative_preferences",
                            "schema_version",
                        ),
                        fallback="use_universal_kernel_and_custom_world",
                    ),
                    ConsumerBinding(
                        consumer="on_demand_confirmation",
                        stage="confirmation",
                        fields=("pack_id", "version", "rules", "lifecycles"),
                        required=False,
                        fallback="keep_candidates_inactive",
                    ),
                ),
                retention="project_versioned",
            ),
            ArtifactConsumerContract(
                artifact_type="ProjectWorldConstitution",
                producer="confirmed_world_mapping",
                authority="project_rule_authority",
                owns_semantics=True,
                semantic_keys=("world_rules", "lifecycle_definitions"),
                stable_fields=(
                    "project_id",
                    "version",
                    "rules",
                    "lifecycles",
                    "bound_candidate_packs",
                    "schema_version",
                ),
                consumers=(
                    ConsumerBinding(
                        consumer="runtime_compiler",
                        stage="runtime_compile",
                        fields=(
                            "project_id",
                            "version",
                            "rules",
                            "lifecycles",
                            "bound_candidate_packs",
                            "schema_version",
                        ),
                        fallback="compile_explicit_rules_only_and_report_partial",
                    ),
                    ConsumerBinding(
                        consumer="world_runtime_debug_api",
                        stage="debug_api",
                        fields=("project_id", "version", "bound_candidate_packs"),
                        required=False,
                        fallback="omit_debug_projection",
                    ),
                ),
                retention="project_versioned",
            ),
            ArtifactConsumerContract(
                artifact_type="CanonicalWorldState",
                producer="accepted_state_delta_committer",
                authority="current_world_state_authority",
                owns_semantics=True,
                semantic_keys=("current_world_facts",),
                stable_fields=("project_id", "revision", "facts", "schema_version"),
                consumers=(
                    ConsumerBinding(
                        consumer="state_frame_builder",
                        stage="checkpoint",
                        fields=("project_id", "revision", "facts", "schema_version"),
                        fallback="emit_partial_state_frame_without_inference",
                    ),
                    ConsumerBinding(
                        consumer="runtime_compiler",
                        stage="runtime_compile",
                        fields=("project_id", "revision", "facts"),
                        fallback="compile_partial_frame_with_unknowns",
                    ),
                    ConsumerBinding(
                        consumer="transition_validator",
                        stage="transition_validate",
                        fields=("project_id", "revision", "facts"),
                        fallback="return_unresolved_not_invalid",
                    ),
                ),
                retention="permanent_audit",
            ),
            ArtifactConsumerContract(
                artifact_type="NarrativePolicy",
                producer="narrative_policy_system",
                authority="narrative_preference_only",
                owns_semantics=True,
                semantic_keys=("narrative_preferences",),
                stable_fields=(
                    "project_id",
                    "version",
                    "preferences",
                    "schema_version",
                ),
                consumers=(
                    ConsumerBinding(
                        consumer="writer_policy_renderer",
                        stage="prompt_render",
                        fields=(
                            "project_id",
                            "version",
                            "preferences",
                            "schema_version",
                        ),
                        fallback="omit_narrative_policy",
                    ),
                    ConsumerBinding(
                        consumer="reviewer",
                        stage="review",
                        fields=("project_id", "version", "preferences"),
                        required=False,
                        fallback="review_without_policy_attribution",
                    ),
                ),
                retention="project_versioned",
            ),
        )
    )


def build_wr0b_consumer_registry() -> RuntimeArtifactConsumerRegistry:
    """Extend WR0-A ownership with resolver inputs and outputs."""

    from .world_runtime_resolver import (
        ConflictReport,
        ResolvedWorldConstitution,
        UserOverrideSet,
        WORLD_RUNTIME_RESOLVER_VERSION,
    )

    base = build_wr0a_consumer_registry()
    additions = (
        ArtifactConsumerContract(
            artifact_type="UserOverrideSet",
            producer="on_demand_confirmation",
            authority="user_override_authority",
            owns_semantics=True,
            semantic_keys=("user_world_rule_overrides",),
            stable_fields=tuple(UserOverrideSet.model_fields),
            consumers=(
                ConsumerBinding(
                    consumer="world_runtime_resolver",
                    stage="world_mapping",
                    fields=tuple(UserOverrideSet.model_fields),
                    fallback="resolve_without_unavailable_overrides",
                ),
                ConsumerBinding(
                    consumer="world_runtime_debug_api",
                    stage="debug_api",
                    fields=("project_id", "version", "schema_version"),
                    required=False,
                    fallback="omit_override_debug_projection",
                ),
            ),
            retention="project_versioned",
        ),
        ArtifactConsumerContract(
            artifact_type="ResolvedWorldConstitution",
            producer="world_runtime_resolver",
            authority="derived_resolved_rule_projection",
            owns_semantics=False,
            semantic_keys=("world_rules", "lifecycle_definitions"),
            stable_fields=tuple(ResolvedWorldConstitution.model_fields),
            consumers=(
                ConsumerBinding(
                    consumer="runtime_compiler",
                    stage="runtime_compile",
                    fields=tuple(ResolvedWorldConstitution.model_fields),
                    fallback="stop_on_blocking_conflict_or_compile_explicit_partial",
                ),
                ConsumerBinding(
                    consumer="world_runtime_debug_api",
                    stage="debug_api",
                    fields=(
                        "project_id",
                        "constitution_version",
                        "override_version",
                        "bound_candidate_packs",
                        "decisions",
                        "conflict_report",
                        "schema_version",
                    ),
                    required=False,
                    fallback="omit_resolver_debug_projection",
                ),
            ),
            retention="project_versioned",
        ),
        ArtifactConsumerContract(
            artifact_type="ConflictReport",
            producer="world_runtime_resolver",
            authority="resolver_conflict_authority",
            owns_semantics=True,
            semantic_keys=("world_rule_conflicts",),
            stable_fields=tuple(ConflictReport.model_fields),
            consumers=(
                ConsumerBinding(
                    consumer="on_demand_confirmation",
                    stage="confirmation",
                    fields=tuple(ConflictReport.model_fields),
                    fallback="preserve_blocking_conflict_unresolved",
                ),
                ConsumerBinding(
                    consumer="runtime_compiler_gate",
                    stage="runtime_compile",
                    fields=("project_id", "conflicts", "schema_version"),
                    fallback="refuse_silent_conflict_resolution",
                ),
                ConsumerBinding(
                    consumer="world_runtime_debug_api",
                    stage="debug_api",
                    fields=("project_id", "conflicts", "schema_version"),
                    required=False,
                    fallback="omit_conflict_debug_projection",
                ),
            ),
            retention="permanent_audit",
        ),
    )
    return RuntimeArtifactConsumerRegistry(
        contracts=(*base.contracts, *additions),
        version=WORLD_RUNTIME_RESOLVER_VERSION,
    )


def build_wr0c_consumer_registry() -> RuntimeArtifactConsumerRegistry:
    """Extend WR0-B ownership with the minimal kernel and its gate report."""

    from .world_runtime_kernel import (
        KernelValidationReport,
        UniversalRuntimeKernel,
        WORLD_RUNTIME_KERNEL_VERSION,
    )

    base = build_wr0b_consumer_registry()
    additions = (
        ArtifactConsumerContract(
            artifact_type="UniversalRuntimeKernel",
            producer="universal_kernel_repository",
            authority="universal_meta_rule_authority",
            owns_semantics=True,
            semantic_keys=("runtime_meta_rules",),
            stable_fields=tuple(UniversalRuntimeKernel.model_fields),
            consumers=(
                ConsumerBinding(
                    consumer="kernel_integrity_validator",
                    stage="world_mapping",
                    fields=tuple(UniversalRuntimeKernel.model_fields),
                    fallback="reject_invalid_kernel_without_genre_inference",
                ),
                ConsumerBinding(
                    consumer="world_runtime_resolver",
                    stage="world_mapping",
                    fields=("kernel_id", "version", "rules", "schema_version"),
                    fallback="emit_invalid_universal_kernel_conflict",
                ),
                ConsumerBinding(
                    consumer="runtime_compiler",
                    stage="runtime_compile",
                    fields=("kernel_id", "version", "rules", "schema_version"),
                    required=False,
                    fallback="do_not_compile_runtime_frame",
                ),
            ),
            retention="project_versioned",
        ),
        ArtifactConsumerContract(
            artifact_type="KernelValidationReport",
            producer="kernel_integrity_validator",
            authority="kernel_integrity_gate",
            owns_semantics=True,
            semantic_keys=("kernel_integrity_status",),
            stable_fields=tuple(KernelValidationReport.model_fields),
            consumers=(
                ConsumerBinding(
                    consumer="wr0c_preflight_gate",
                    stage="world_mapping",
                    fields=tuple(KernelValidationReport.model_fields),
                    fallback="stop_before_kernel_consumption",
                ),
                ConsumerBinding(
                    consumer="world_runtime_debug_api",
                    stage="debug_api",
                    fields=(
                        "kernel_id",
                        "kernel_hash",
                        "valid",
                        "issues",
                        "schema_version",
                    ),
                    required=False,
                    fallback="omit_kernel_debug_projection",
                ),
            ),
            retention="permanent_audit",
        ),
    )
    return RuntimeArtifactConsumerRegistry(
        contracts=(*base.contracts, *additions),
        version=WORLD_RUNTIME_KERNEL_VERSION,
    )


def build_wr0e_consumer_registry() -> RuntimeArtifactConsumerRegistry:
    """Register WR0-E gold artifacts without granting production authority."""

    from .world_runtime_bakery_gold import (
        GoldCommittedStateDelta,
        GoldValidationResult,
        ProposedStateDelta,
        SaturdayBakeryGoldFixture,
        WORLD_RUNTIME_BAKERY_GOLD_VERSION,
    )
    from .world_runtime_event_contracts import SubsectionEventContract

    base = build_wr0c_consumer_registry()
    artifact_specs = (
        (
            "SubsectionEventContract",
            SubsectionEventContract,
            "wr0e_gold_fixture_builder",
            "offline_gold_event_contract",
            "gold_event_requirements",
            (
                ConsumerBinding(
                    consumer="wr0e_gold_chain_audit",
                    stage="transition_validate",
                    fields=tuple(SubsectionEventContract.model_fields),
                    fallback="fail_gold_fixture_construction",
                ),
                ConsumerBinding(
                    consumer="wr0f_runtime_compiler_fixture",
                    stage="runtime_compile",
                    fields=(
                        "contract_id",
                        "project_id",
                        "section",
                        "subsection",
                        "requirements",
                        "provenance",
                        "schema_version",
                    ),
                    required=False,
                    fallback="keep_wr0f_not_started",
                ),
            ),
        ),
        (
            "ProposedStateDelta",
            ProposedStateDelta,
            "wr0e_gold_fixture_builder",
            "offline_non_authoritative_gold_delta",
            "gold_proposed_state_changes",
            (
                ConsumerBinding(
                    consumer="wr0e_gold_chain_audit",
                    stage="transition_validate",
                    fields=tuple(ProposedStateDelta.model_fields),
                    fallback="fail_gold_fixture_construction",
                ),
                ConsumerBinding(
                    consumer="wr2_validator_fixture",
                    stage="transition_validate",
                    fields=tuple(ProposedStateDelta.model_fields),
                    required=False,
                    fallback="keep_wr2_not_started",
                ),
            ),
        ),
        (
            "GoldValidationResult",
            GoldValidationResult,
            "wr0e_gold_fixture_builder",
            "offline_expected_validation_only",
            "gold_transition_outcomes",
            (
                ConsumerBinding(
                    consumer="wr0e_gold_chain_audit",
                    stage="review",
                    fields=tuple(GoldValidationResult.model_fields),
                    fallback="fail_gold_fixture_construction",
                ),
                ConsumerBinding(
                    consumer="wr2_validator_oracle",
                    stage="transition_validate",
                    fields=tuple(GoldValidationResult.model_fields),
                    required=False,
                    fallback="keep_wr2_not_started",
                ),
            ),
        ),
        (
            "GoldCommittedStateDelta",
            GoldCommittedStateDelta,
            "wr0e_gold_fixture_builder",
            "offline_expected_commit_only",
            "gold_committed_state_changes",
            (
                ConsumerBinding(
                    consumer="wr0e_state_after_projection",
                    stage="state_commit",
                    fields=tuple(GoldCommittedStateDelta.model_fields),
                    fallback="fail_gold_fixture_construction",
                ),
            ),
        ),
        (
            "SaturdayBakeryGoldFixture",
            SaturdayBakeryGoldFixture,
            "wr0e_gold_fixture_builder",
            "offline_regression_fixture",
            "gold_chain_bundle",
            (
                ConsumerBinding(
                    consumer="wr0e_regression_suite",
                    stage="review",
                    fields=tuple(SaturdayBakeryGoldFixture.model_fields),
                    fallback="fail_wr0e_acceptance",
                ),
            ),
        ),
    )
    additions = tuple(
        ArtifactConsumerContract(
            artifact_type=artifact_type,
            producer=producer,
            authority=authority,
            owns_semantics=False,
            semantic_keys=(semantic_key,),
            stable_fields=tuple(model.model_fields),
            consumers=consumers,
            retention="transient",
            version=WORLD_RUNTIME_BAKERY_GOLD_VERSION,
        )
        for (
            artifact_type,
            model,
            producer,
            authority,
            semantic_key,
            consumers,
        ) in artifact_specs
    )
    return RuntimeArtifactConsumerRegistry(
        contracts=(*base.contracts, *additions),
        version=WORLD_RUNTIME_BAKERY_GOLD_VERSION,
    )


def build_wr0f_consumer_registry() -> RuntimeArtifactConsumerRegistry:
    """Register the read-only scene frame and keep production consumers optional."""

    from .world_runtime_compiler import (
        SceneRuntimeFrame,
        WORLD_RUNTIME_COMPILER_VERSION,
    )

    base = build_wr0e_consumer_registry()
    frame_contract = ArtifactConsumerContract(
        artifact_type="SceneRuntimeFrame",
        producer="world_runtime_compiler",
        authority="derived_subsection_runtime_boundary",
        owns_semantics=False,
        semantic_keys=("scene_runtime_projection",),
        stable_fields=tuple(SceneRuntimeFrame.model_fields),
        consumers=(
            ConsumerBinding(
                consumer="wr0f_regression_suite",
                stage="review",
                fields=tuple(SceneRuntimeFrame.model_fields),
                fallback="fail_wr0f_acceptance",
            ),
            ConsumerBinding(
                consumer="writer_runtime_renderer",
                stage="prompt_render",
                fields=(
                    "frame_id",
                    "project_id",
                    "section",
                    "subsection",
                    "state_revision",
                    "status",
                    "facts",
                    "activated_rules",
                    "transition_options",
                    "event_boundaries",
                    "unknowns",
                    "schema_version",
                ),
                required=False,
                fallback="shadow_only_no_prompt_injection",
            ),
            ConsumerBinding(
                consumer="runtime_transition_validator",
                stage="transition_validate",
                fields=(
                    "frame_id",
                    "project_id",
                    "state_revision",
                    "relevant_source_hash",
                    "status",
                    "facts",
                    "activated_rules",
                    "activated_lifecycles",
                    "transition_options",
                    "event_boundaries",
                    "unknowns",
                    "issues",
                    "schema_version",
                ),
                required=False,
                fallback="keep_wr2_not_started",
            ),
            ConsumerBinding(
                consumer="world_runtime_debug_api",
                stage="debug_api",
                fields=(
                    "frame_id",
                    "constitution_version",
                    "kernel_version",
                    "event_contract_id",
                    "event_contract_hash",
                    "relevant_source_hash",
                    "status",
                    "excluded_artifacts",
                    "issues",
                    "schema_version",
                ),
                required=False,
                fallback="omit_runtime_frame_debug_projection",
            ),
        ),
        retention="transient",
        version=WORLD_RUNTIME_COMPILER_VERSION,
    )
    return RuntimeArtifactConsumerRegistry(
        contracts=(*base.contracts, frame_contract),
        version=WORLD_RUNTIME_COMPILER_VERSION,
    )


def build_wr0g_consumer_registry() -> RuntimeArtifactConsumerRegistry:
    """Register confirmation audit artifacts and the read-only debug view."""

    from .world_runtime_confirmation import (
        ConfirmationDecisionLedger,
        ConstitutionChangeSet,
        WorldRuntimeDebugView,
        WORLD_RUNTIME_CONFIRMATION_VERSION,
    )

    base = build_wr0f_consumer_registry()
    additions = (
        ArtifactConsumerContract(
            artifact_type="ConfirmationDecisionLedger",
            producer="on_demand_confirmation",
            authority="user_confirmation_decision_authority",
            owns_semantics=True,
            semantic_keys=("candidate_confirmation_decisions",),
            stable_fields=tuple(ConfirmationDecisionLedger.model_fields),
            consumers=(
                ConsumerBinding(
                    consumer="confirmation_queue_builder",
                    stage="confirmation",
                    fields=tuple(ConfirmationDecisionLedger.model_fields),
                    fallback="show_only_undecided_relevant_candidates",
                ),
                ConsumerBinding(
                    consumer="world_runtime_debug_projection",
                    stage="debug_api",
                    fields=("project_id", "version", "records", "schema_version"),
                    required=False,
                    fallback="omit_decision_history",
                ),
            ),
            retention="permanent_audit",
            version=WORLD_RUNTIME_CONFIRMATION_VERSION,
        ),
        ArtifactConsumerContract(
            artifact_type="ConstitutionChangeSet",
            producer="on_demand_confirmation",
            authority="user_requested_constitution_mutation",
            owns_semantics=True,
            semantic_keys=("versioned_constitution_changes",),
            stable_fields=tuple(ConstitutionChangeSet.model_fields),
            consumers=(
                ConsumerBinding(
                    consumer="constitution_change_replayer",
                    stage="confirmation",
                    fields=tuple(ConstitutionChangeSet.model_fields),
                    fallback="reject_base_version_or_hash_mismatch",
                ),
                ConsumerBinding(
                    consumer="world_runtime_debug_projection",
                    stage="debug_api",
                    fields=(
                        "change_set_id",
                        "project_id",
                        "base_constitution_version",
                        "base_constitution_hash",
                        "resulting_constitution_version",
                        "decision_records",
                        "schema_version",
                    ),
                    required=False,
                    fallback="omit_change_history",
                ),
            ),
            retention="permanent_audit",
            version=WORLD_RUNTIME_CONFIRMATION_VERSION,
        ),
        ArtifactConsumerContract(
            artifact_type="WorldRuntimeDebugView",
            producer="world_runtime_debug_projection",
            authority="read_only_runtime_debug_projection",
            owns_semantics=False,
            semantic_keys=("runtime_debug_view",),
            stable_fields=tuple(WorldRuntimeDebugView.model_fields),
            consumers=(
                ConsumerBinding(
                    consumer="wr0g_json_yaml_renderer",
                    stage="debug_api",
                    fields=tuple(WorldRuntimeDebugView.model_fields),
                    fallback="omit_debug_view_without_changing_runtime",
                ),
            ),
            retention="transient",
            version=WORLD_RUNTIME_CONFIRMATION_VERSION,
        ),
    )
    return RuntimeArtifactConsumerRegistry(
        contracts=(*base.contracts, *additions),
        version=WORLD_RUNTIME_CONFIRMATION_VERSION,
    )


def build_wr1_consumer_registry() -> RuntimeArtifactConsumerRegistry:
    """Register the compact Writer projection and its rollout observation."""

    from .world_runtime_prompt import (
        RuntimePromptObservation,
        RuntimePromptProjection,
        WORLD_RUNTIME_PROMPT_VERSION,
    )

    base = build_wr0g_consumer_registry()
    additions = (
        ArtifactConsumerContract(
            artifact_type="RuntimePromptProjection",
            producer="world_runtime_prompt_compiler",
            authority="derived_writer_runtime_boundary",
            owns_semantics=False,
            semantic_keys=("writer_runtime_projection",),
            stable_fields=tuple(RuntimePromptProjection.model_fields),
            consumers=(
                ConsumerBinding(
                    consumer="allowlisted_writer_runtime_renderer",
                    stage="prompt_render",
                    fields=tuple(RuntimePromptProjection.model_fields),
                    fallback="omit_runtime_prompt_and_preserve_baseline_messages",
                ),
                ConsumerBinding(
                    consumer="wr1_regression_suite",
                    stage="review",
                    fields=tuple(RuntimePromptProjection.model_fields),
                    fallback="fail_wr1_pre_generation_gate",
                ),
            ),
            retention="transient",
            version=WORLD_RUNTIME_PROMPT_VERSION,
        ),
        ArtifactConsumerContract(
            artifact_type="RuntimePromptObservation",
            producer="world_runtime_prompt_controller",
            authority="runtime_prompt_rollout_audit",
            owns_semantics=False,
            semantic_keys=("writer_runtime_rollout_observation",),
            stable_fields=tuple(RuntimePromptObservation.model_fields),
            consumers=(
                ConsumerBinding(
                    consumer="wr1_canary_ledger",
                    stage="review",
                    fields=tuple(RuntimePromptObservation.model_fields),
                    fallback="block_promotion_when_observation_missing",
                ),
            ),
            retention="permanent_audit",
            version=WORLD_RUNTIME_PROMPT_VERSION,
        ),
    )
    return RuntimeArtifactConsumerRegistry(
        contracts=(*base.contracts, *additions),
        version=WORLD_RUNTIME_PROMPT_VERSION,
    )
