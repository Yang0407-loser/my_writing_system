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
from .shadow_validation import ShadowBoundaryValidationRunner
from .post_write_extraction import SharedPostWriteExtractor
from .shadow_post_write_extraction import ShadowPostWriteExtractionRunner
from .state_frame import StateFrameCompiler

__all__ = [
    "CommitArtifact",
    "GenerationArtifact",
    "GenerationController",
    "MandatoryEventDetection",
    "MandatoryEventPolicy",
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
    "ShadowBoundaryValidationRunner",
    "ShadowPostWriteExtractionRunner",
    "SharedPostWriteExtractor",
]
