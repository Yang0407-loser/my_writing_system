"""Explicit canonical concurrency and projection errors."""


class CanonicalError(Exception):
    """Base class for canonical runtime failures."""


class RevisionConflict(CanonicalError):
    """The subsection Revision Head no longer matches the candidate base."""


class StateVersionConflict(CanonicalError):
    """The Project State Head no longer matches the candidate base."""


class IdempotencyConflict(CanonicalError):
    """An idempotency key was reused with a different candidate hash."""


class ProjectionBarrierPending(CanonicalError):
    """Critical projections have not reached the committed canonical state."""


class ScopeRequired(CanonicalError):
    """A canonical operation omitted mandatory tenant or project scope."""
