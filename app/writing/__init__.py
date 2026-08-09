"""Behavior-preserving boundaries for the subsection writing loop."""

from .contracts import (
    CommitArtifact,
    PostWriteEvidence,
    PostWriteStateBundle,
    PostWriteStateChange,
    SceneSpec,
    SourceEvidence,
    StateFrame,
    StateAssertion,
    StoryStateSnapshot,
    GenerationArtifact,
    PromptArtifact,
    SubsectionInput,
    SubsectionPipelineArtifact,
)
from .generation_controller import GenerationController
from .mandatory_event_policy import MandatoryEventDetection, MandatoryEventPolicy
from .outline_event_contract import (
    ChapterEventContract,
    LegacyOutlineEventAdapter,
    OutlineEventContractCompiler,
    OutlineEventUnit,
    SubsectionEventContract,
)
from .character_arc_projection import (
    ArcProjectionCandidate,
    ChapterCharacterArcProjection,
    CharacterArcProjection,
    CharacterArcProjector,
)
from .prompt_builder import PromptBuilder
from .scene_spec_provider import (
    OutlineSceneSpecProvider,
    SceneSpecApplication,
    SceneSpecBuildResult,
    SceneSpecCanaryController,
)
from .state_committer import StateCommitter
from .character_state_propagation import (
    build_character_state_propagation_event,
    character_arcs_hash,
    copy_character_arcs,
    is_valid_character_arcs,
    resolve_writer_character_arcs,
)
from .subsection_pipeline import SubsectionPipeline
from .subsection_generator import SubsectionGenerator
from .legacy_subsection_projection import (
    LegacyProjectionEnvelope,
    LegacyProjectionError,
    LegacySubsectionProjection,
)
from .canonical_subsection_runtime import (
    CanonicalSubsectionCommand,
    CanonicalSubsectionRuntime,
    CanonicalSubsectionRuntimeResult,
    canonical_idempotency_key,
)
from .shadow_validation import ShadowBoundaryValidationRunner
from .post_write_extraction import SharedPostWriteExtractor
from .shadow_post_write_extraction import ShadowPostWriteExtractionRunner
from .state_frame import StateFrameCompiler
from .writer_execution_contract import (
    ExecutionSourceRef,
    WriterExecutionContract,
    WriterExecutionContractApplication,
    WriterExecutionContractBuildResult,
    WriterExecutionContractController,
    WriterExecutionContractProvider,
)
from .commercial_narrative_harness import (
    CommercialNarrativeHarness,
    classify_scene,
    compile_commercial_narrative_harness,
    harness_hash,
    render_commercial_narrative_harness,
)
from .narrative_integrity import (
    NarrativeIntegrityPolicy,
    WorldPressureContract,
    compile_narrative_integrity,
    compile_world_pressure_contract,
    compose_narrative_control_context,
    narrative_integrity_hash,
    render_narrative_integrity,
    render_world_pressure_contract,
    world_pressure_hash,
)
from .narrative_reality_checks import NarrativeRealityChecker, RealityWarning
from .anti_ai_expression_kernel import (
    ANTI_AI_EXPRESSION_KERNEL_V0,
    KERNEL_VERSION as ANTI_AI_EXPRESSION_KERNEL_VERSION,
    AntiAIExpressionController,
    ExpressionKernel,
    expression_kernel_hash,
    normalize_anti_ai_expression_mode,
    render_expression_kernel,
)

__all__ = [
    "CommitArtifact",
    "GenerationArtifact",
    "GenerationController",
    "MandatoryEventDetection",
    "MandatoryEventPolicy",
    "ChapterEventContract",
    "LegacyOutlineEventAdapter",
    "OutlineEventContractCompiler",
    "OutlineEventUnit",
    "SubsectionEventContract",
    "ArcProjectionCandidate",
    "ChapterCharacterArcProjection",
    "CharacterArcProjection",
    "CharacterArcProjector",
    "PostWriteEvidence",
    "PostWriteStateBundle",
    "PostWriteStateChange",
    "PromptArtifact",
    "PromptBuilder",
    "OutlineSceneSpecProvider",
    "SceneSpec",
    "SceneSpecApplication",
    "SceneSpecBuildResult",
    "SceneSpecCanaryController",
    "SourceEvidence",
    "StateFrame",
    "StateFrameCompiler",
    "StateAssertion",
    "StoryStateSnapshot",
    "StateCommitter",
    "build_character_state_propagation_event",
    "character_arcs_hash",
    "copy_character_arcs",
    "is_valid_character_arcs",
    "resolve_writer_character_arcs",
    "SubsectionInput",
    "SubsectionPipeline",
    "SubsectionPipelineArtifact",
    "SubsectionGenerator",
    "LegacyProjectionEnvelope",
    "LegacyProjectionError",
    "LegacySubsectionProjection",
    "CanonicalSubsectionCommand",
    "CanonicalSubsectionRuntime",
    "CanonicalSubsectionRuntimeResult",
    "canonical_idempotency_key",
    "ShadowBoundaryValidationRunner",
    "ShadowPostWriteExtractionRunner",
    "SharedPostWriteExtractor",
    "ExecutionSourceRef",
    "WriterExecutionContract",
    "WriterExecutionContractApplication",
    "WriterExecutionContractBuildResult",
    "WriterExecutionContractController",
    "WriterExecutionContractProvider",
    "CommercialNarrativeHarness",
    "classify_scene",
    "compile_commercial_narrative_harness",
    "harness_hash",
    "render_commercial_narrative_harness",
    "NarrativeIntegrityPolicy",
    "WorldPressureContract",
    "compile_narrative_integrity",
    "compile_world_pressure_contract",
    "compose_narrative_control_context",
    "narrative_integrity_hash",
    "render_narrative_integrity",
    "render_world_pressure_contract",
    "world_pressure_hash",
    "NarrativeRealityChecker",
    "RealityWarning",
    "ANTI_AI_EXPRESSION_KERNEL_V0",
    "ANTI_AI_EXPRESSION_KERNEL_VERSION",
    "AntiAIExpressionController",
    "ExpressionKernel",
    "expression_kernel_hash",
    "normalize_anti_ai_expression_mode",
    "render_expression_kernel",
]
