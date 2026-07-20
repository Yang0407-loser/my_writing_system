"""Traceable state machine for one subsection; R1 keeps Writer as its facade."""

from __future__ import annotations

import uuid

from .contracts import (
    CommitArtifact,
    GenerationArtifact,
    PromptArtifact,
    SubsectionInput,
    SubsectionPipelineArtifact,
)


class SubsectionPipeline:
    PHASES = ("prepared", "assembled", "generated", "validated", "committed")

    def __init__(self, prepared: SubsectionInput, trace_id: str | None = None) -> None:
        self.prepared = prepared
        self.trace_id = trace_id or str(uuid.uuid4())
        self.prompt: PromptArtifact | None = None
        self.generation: GenerationArtifact | None = None
        self.validation: dict | None = None
        self.commit: CommitArtifact | None = None
        self.phase_history = ["prepared"]

    @property
    def phase(self) -> str:
        return self.phase_history[-1]

    def record_prompt(self, artifact: PromptArtifact) -> None:
        self._advance("prepared", "assembled")
        self.prompt = artifact

    def record_generation(self, artifact: GenerationArtifact) -> None:
        self._advance("assembled", "generated")
        self.generation = artifact

    def record_validation(self, result: dict) -> None:
        self._advance("generated", "validated")
        self.validation = dict(result)

    def record_commit(self, artifact: CommitArtifact) -> None:
        self._advance("validated", "committed")
        self.commit = artifact

    def _advance(self, expected: str, next_phase: str) -> None:
        if self.phase != expected:
            raise RuntimeError(f"invalid subsection phase transition: {self.phase} -> {next_phase}")
        self.phase_history.append(next_phase)

    def artifact(self) -> SubsectionPipelineArtifact:
        return SubsectionPipelineArtifact(
            trace_id=self.trace_id,
            phase=self.phase,
            prepared=self.prepared,
            prompt=self.prompt,
            generation=self.generation,
            validation=self.validation,
            commit=self.commit,
            phase_history=list(self.phase_history),
        )
