"""Identity value objects.

Value objects are immutable, validated at construction, and compared by value.
They use ``str`` mixins so they serialise naturally without extra glue code.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import StrEnum

from notes_service.domain.errors import ValidationError

# Pragmatic, deliberately-not-RFC-5322 email check. Over-strict regexes reject
# valid addresses; the authoritative check is delivery, which is out of scope.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_MAX_EMAIL_LENGTH = 320  # RFC 5321 forward-path limit


class UserStatus(StrEnum):
    """Lifecycle state of a user account.

    ``ACTIVE`` users may authenticate and act. ``SUSPENDED`` and ``DISABLED``
    users are resolvable but must be rejected by the identity provider, so a
    deactivated account immediately loses access without deleting its data.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"

    @property
    def can_authenticate(self) -> bool:
        return self is UserStatus.ACTIVE


class Email(str):
    """A validated, normalised (lower-cased) email address."""

    __slots__ = ()

    def __new__(cls, value: str) -> Email:
        if not isinstance(value, str):
            raise ValidationError("email must be a string")
        candidate = value.strip().lower()
        if not candidate:
            raise ValidationError("email must not be empty")
        if len(candidate) > _MAX_EMAIL_LENGTH:
            raise ValidationError(f"email must be at most {_MAX_EMAIL_LENGTH} characters")
        if not _EMAIL_RE.match(candidate):
            raise ValidationError("email must be a valid address")
        return super().__new__(cls, candidate)

    @property
    def domain(self) -> str:
        return self.rsplit("@", 1)[1]


class UserId(uuid.UUID):
    """Strongly-typed user identifier."""

    __slots__ = ()

    @classmethod
    def new(cls) -> UserId:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str | uuid.UUID) -> UserId:
        if isinstance(value, UserId):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid user id: {value!r}") from exc


class ExternalIdentityId(uuid.UUID):
    """Strongly-typed identifier for an external identity record."""

    __slots__ = ()

    @classmethod
    def new(cls) -> ExternalIdentityId:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str | uuid.UUID) -> ExternalIdentityId:
        if isinstance(value, ExternalIdentityId):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid external identity id: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class ProviderSubject:
    """The (provider, subject) pair that identifies a user at an IdP.

    This is the natural key used to link an external login to a local user.
    """

    provider: str
    subject: str

    def __post_init__(self) -> None:
        if not self.provider or not self.provider.strip():
            raise ValidationError("provider must not be empty")
        if not self.subject or not self.subject.strip():
            raise ValidationError("subject must not be empty")
        if len(self.provider) > 64:
            raise ValidationError("provider must be at most 64 characters")
        if len(self.subject) > 255:
            raise ValidationError("subject must be at most 255 characters")
