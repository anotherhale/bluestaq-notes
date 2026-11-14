"""Identity entities: :class:`User` and :class:`ExternalIdentity`."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from notes_service.domain.errors import InvariantViolation
from notes_service.domain.identity.value_objects import (
    Email,
    ExternalIdentityId,
    ProviderSubject,
    UserId,
    UserStatus,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class User:
    """An application user.

    The ``User`` is the local representation of a person. External login
    credentials live in :class:`ExternalIdentity`, keeping authentication
    concerns out of the user record itself.
    """

    id: UserId
    email: Email
    display_name: str
    status: UserStatus = UserStatus.ACTIVE
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    MAX_DISPLAY_NAME_LENGTH = 200

    def __post_init__(self) -> None:
        name = self.display_name.strip()
        if not name:
            raise InvariantViolation("display_name must not be empty")
        if len(name) > self.MAX_DISPLAY_NAME_LENGTH:
            raise InvariantViolation(
                f"display_name must be at most {self.MAX_DISPLAY_NAME_LENGTH} characters"
            )
        self.display_name = name

    @classmethod
    def register(
        cls,
        *,
        email: Email | str,
        display_name: str,
        status: UserStatus = UserStatus.ACTIVE,
        user_id: UserId | None = None,
    ) -> User:
        """Factory for creating a brand-new user with a generated identifier."""
        return cls(
            id=user_id or UserId.new(),
            email=Email(email) if not isinstance(email, Email) else email,
            display_name=display_name,
            status=status,
        )

    @property
    def is_active(self) -> bool:
        return self.status.can_authenticate

    def rename(self, display_name: str, *, now: datetime | None = None) -> None:
        replacement = replace(self, display_name=display_name)
        self.display_name = replacement.display_name
        self.updated_at = now or _utcnow()

    def change_status(self, status: UserStatus, *, now: datetime | None = None) -> None:
        self.status = status
        self.updated_at = now or _utcnow()


@dataclass(slots=True)
class ExternalIdentity:
    """A link between a local :class:`User` and an external identity provider.

    ``(provider, subject)`` is unique across the system: a given login at a
    given provider maps to exactly one local user.
    """

    id: ExternalIdentityId
    user_id: UserId
    provider_subject: ProviderSubject
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def link(
        cls,
        *,
        user_id: UserId,
        provider: str,
        subject: str,
        identity_id: ExternalIdentityId | None = None,
    ) -> ExternalIdentity:
        return cls(
            id=identity_id or ExternalIdentityId.new(),
            user_id=user_id,
            provider_subject=ProviderSubject(provider=provider, subject=subject),
        )

    @property
    def provider(self) -> str:
        return self.provider_subject.provider

    @property
    def subject(self) -> str:
        return self.provider_subject.subject
