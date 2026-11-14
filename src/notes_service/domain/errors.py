"""Domain-level error taxonomy.

These exceptions describe violations of business rules and carry no knowledge of
HTTP or any transport. The presentation layer is responsible for translating
them into transport-appropriate responses.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every error raised by the domain layer."""


class ValidationError(DomainError):
    """A value object or request-derived value failed validation.

    Raised for invalid *input* (for example an over-long title or a malformed
    identifier) rather than for a rule that depends on current state.
    """


class InvariantViolation(DomainError):
    """An operation would break an invariant of an aggregate.

    Examples: a non-owner attempting to grant a permission, or transferring
    ownership. These are safety-net failures; the application layer normally
    rejects such requests earlier via explicit authorization.
    """


class VersionConflict(DomainError):
    """An optimistic-concurrency check failed.

    A client attempted to update an entity using a version that is no longer
    current. The caller must reload the entity and retry.
    """

    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"version conflict: expected version {expected} but current version is {actual}"
        )
