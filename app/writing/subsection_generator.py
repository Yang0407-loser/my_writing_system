"""Side-effect-free Writer seam that produces an immutable Candidate."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..canonical.contracts import (
    CandidateValidation,
    CanonicalStateSnapshot,
    SubsectionCandidate,
)
from ..canonical.legacy_candidate_adapter import adapt_legacy_handover
from .contracts import SubsectionInput
from .generation_controller import GenerationController
from .prompt_builder import PromptBuilder


class SubsectionGenerator:
    """Generate and validate one subsection without mutating runtime stores.

    The only impure dependency is the LLM-backed generation/extraction boundary.
    World, event, vector, context, checkpoint and stream writes are deliberately
    absent; they belong to post-commit projections.
    """

    def __init__(
        self,
        *,
        generation_controller: GenerationController,
        handover_extractor: Callable[..., tuple[dict[str, Any] | None, Any]],
        post_validator: Callable[[str], dict[str, Any]],
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.generation_controller = generation_controller
        self.handover_extractor = handover_extractor
        self.post_validator = post_validator
        self.prompt_builder = prompt_builder or PromptBuilder()

    def generate_subsection_candidate(
        self,
        *,
        prepared: SubsectionInput,
        canonical_state_snapshot: CanonicalStateSnapshot,
        tenant_id: str,
        project_id: str,
        document_id: str,
        subsection_id: str,
        ordinal: int,
        title: str,
        topic: str,
        base_revision_number: int,
        mandatory_events_text: str,
        token_by_source: dict[str, int] | None = None,
        characters: list[dict[str, Any]] | None = None,
        previous_texts: list[str] | None = None,
        prev_sub_text: str = "",
        target_goal: str = "",
        character_context: str = "",
        event_graph: Any = None,
        current_subsection: dict[str, Any] | None = None,
        next_subsection: dict[str, Any] | None = None,
        state_frame: dict[str, Any] | None = None,
    ) -> SubsectionCandidate:
        if canonical_state_snapshot.project_id != project_id:
            raise ValueError("canonical state snapshot project scope mismatch")

        prompt = self.prompt_builder.build(
            prepared, token_by_source=token_by_source
        )
        call_max_tokens = int(
            prepared.generation_settings.get("max_tokens")
            or max(200, prepared.target_words * 2)
        )
        generated = self.generation_controller.generate(
            messages=prompt.messages,
            call_max_tokens=call_max_tokens,
            # Canonical candidates are private until Commit succeeds. Streaming
            # uncommitted text would create an external side effect.
            stream_callback=None,
            section_num=prepared.section,
            sub_num=prepared.subsection,
            mandatory_events_text=mandatory_events_text,
            characters=characters,
            previous_texts=previous_texts,
            prev_sub_text=prev_sub_text,
            target_goal=target_goal or prepared.outline_target,
            task_id=prepared.task_id,
        )

        # Length adjustment is part of generation, not a post-validation
        # projection.  Every artifact derived from the prose must therefore
        # observe the final candidate text rather than the pre-adjustment
        # draft.
        adjusted = self.generation_controller.adjust_length(
            generated.draft,
            target_words=prepared.target_words,
            call_max_tokens=call_max_tokens,
            stream_callback=None,
            section_num=prepared.section,
            sub_num=prepared.subsection,
            task_id=prepared.task_id,
        )

        handover, observation = self.handover_extractor(
            section_text=adjusted.draft,
            section_num=prepared.section,
            sub_num=prepared.subsection,
            character_context=character_context,
            event_graph=event_graph,
            current_subsection=current_subsection,
            next_subsection=next_subsection,
            task_id=prepared.task_id,
        )
        validation_payload = dict(self.post_validator(adjusted.draft))
        if not validation_payload.get("complete"):
            raise ValueError("candidate post-validation did not complete")

        adapted = adapt_legacy_handover(
            handover,
            provenance={
                "task_id": prepared.task_id,
                "section": prepared.section,
                "subsection": prepared.subsection,
                "prompt_hash": prompt.messages_hash,
            },
        )
        warnings = tuple(str(item) for item in validation_payload.get("warnings", ()))
        errors = tuple(str(item) for item in validation_payload.get("errors", ()))
        observation_payload = (
            observation.model_dump(mode="json")
            if hasattr(observation, "model_dump")
            else dict(observation or {})
        )
        return SubsectionCandidate.create(
            tenant_id=tenant_id,
            project_id=project_id,
            document_id=document_id,
            subsection_id=subsection_id,
            task_id=prepared.task_id,
            section=prepared.section,
            subsection=prepared.subsection,
            ordinal=ordinal,
            title=title,
            topic=topic,
            base_revision_number=base_revision_number,
            # Preserve the state version that was actually loaded before LLM
            # execution. There is intentionally no latest-Head lookup here.
            base_state_version_id=canonical_state_snapshot.version_id,
            draft=adjusted.draft,
            prompt_hash=prompt.messages_hash,
            validation=CandidateValidation(
                complete=True,
                errors=errors,
                warnings=tuple([*warnings, *adapted.warnings]),
            ),
            handover_candidate=adapted.handover_candidate,
            world_mutations=adapted.world_mutations,
            events=adapted.events,
            state_frame=state_frame,
            generation_metadata={
                "prompt_content_hash": prompt.content_hash,
                "prompt_version": prompt.prompt_version,
                "raw_output_hash": generated.output_hash,
                "generation_attempts": generated.generation_attempts,
                "finish_reason": adjusted.finish_reason,
                "handover_observation": observation_payload,
            },
        )
