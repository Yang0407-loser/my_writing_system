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
from .outbox import OutboxDispatcher
from .projection_barrier import ProjectionBarrier
from .projection_ports import ProjectionMessage, ProjectionPort

__all__ = [
    "CandidateValidation",
    "CanonicalCommitResult",
    "CanonicalEventCandidate",
    "CanonicalStateSnapshot",
    "PreparedCanonicalCommit",
    "StateTransitionResult",
    "SubsectionCandidate",
    "WorldMutationCandidate",
    "OutboxDispatcher",
    "ProjectionBarrier",
    "ProjectionMessage",
    "ProjectionPort",
]
