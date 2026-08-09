"""Canonical document and project-state Foundation contracts."""

from .contracts import (
    CandidateValidation,
    CanonicalCommitResult,
    CanonicalEventCandidate,
    CanonicalStateSnapshot,
    PreparedCanonicalCommit,
    StateTransitionResult,
    SubsectionCandidate,
    WorldMutationCandidate,
)

__all__ = [
    "CandidateValidation",
    "CanonicalCommitResult",
    "CanonicalEventCandidate",
    "CanonicalStateSnapshot",
    "PreparedCanonicalCommit",
    "StateTransitionResult",
    "SubsectionCandidate",
    "WorldMutationCandidate",
]
