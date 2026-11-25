"""Application-layer errors.

These represent use-case-level outcomes that are independent of any transport.
The presentation layer maps them onto HTTP status codes in one place.
"""

from __future__ import annotations


class ApplicationError(Exception):
    """Base class for errors raised while executing a use case."""

    code: str = "application_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class AuthenticationError(ApplicationError):
    """The request could not be authenticated (missing/invalid credentials)."""

    code = "unauthenticated"


class AccessDenied(ApplicationError):
    """The caller is authenticated but not permitted to perform the action.

    Maps to HTTP 403. Used only when the caller is already known to have *some*
    access to the resource, so revealing its existence is not a disclosure.
    """

    code = "forbidden"


class ApprovalRequired(AccessDenied):
    """The action is permitted in principle but a human has not yet approved it.

    Distinct from :class:`AccessDenied` because the correct response is to *wait*,
    not to give up: a caller that treats this as a refusal fails work that a later
    human decision would have allowed. It inherits the 403 mapping — the caller is
    known and the resource is not a secret — and remains an ``AccessDenied`` so
    existing refusal handling keeps its meaning.
    """

    code = "approval_required"


class ResourceNotFound(ApplicationError):
    """The resource does not exist, or is deliberately hidden from the caller.

    Maps to HTTP 404. Authorization failures for resources the caller cannot
    read at all are reported as ``ResourceNotFound`` so that the API does not
    leak the existence of other users' private notes.
    """

    code = "not_found"


class Conflict(ApplicationError):
    """The request conflicts with the current state of the resource.

    Maps to HTTP 409. Currently raised for optimistic-concurrency failures.
    """

    code = "conflict"


class InvalidInput(ApplicationError):
    """The request is syntactically valid but semantically unacceptable.

    Maps to HTTP 422. Used for cross-entity validation such as referencing a
    non-existent user.
    """

    code = "invalid_input"


class RateLimited(ApplicationError):
    """The caller exceeded a configured rate limit (HTTP 429)."""

    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after
