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


class ProjectionError(CanonicalError):
    """Base class for explicitly classified projection failures."""


class RetryableProjectionError(ProjectionError):
    """A transient sink or transport failure that may be retried."""


class PermanentProjectionError(ProjectionError):
    """A deterministic failure that must be dead-lettered."""


class ProjectionConflictError(PermanentProjectionError):
    """A deterministic sink conflict that retrying cannot resolve."""


class InvalidCanonPayloadError(PermanentProjectionError):
    """Canon payload validation failed deterministically."""


class UnknownProjectorVersionError(PermanentProjectionError):
    """No implementation exists for the delivery's projector version."""


class RateLimitProjectionError(RetryableProjectionError):
    """A sink rate limit delayed projection delivery."""
