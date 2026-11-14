"""Database seeding for demo users and their external identities.

Seeding is idempotent: running it twice does not create duplicates. It is used
by ``notes-service init-db --seed`` and by the test suite to obtain a known set
of users for multi-user scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from notes_service.domain.identity.entities import ExternalIdentity, User
from notes_service.domain.identity.value_objects import Email, UserId, UserStatus
from notes_service.infrastructure.db.models import UserRow
from notes_service.infrastructure.db.repositories import (
    SqlAlchemyExternalIdentityRepository,
    SqlAlchemyUserRepository,
)


@dataclass(frozen=True, slots=True)
class DemoUserSpec:
    """A deterministic demo user used for local runs and tests."""

    email: str
    display_name: str
    # A stable UUID makes curl examples and docs reproducible.
    id: str
    status: UserStatus = UserStatus.ACTIVE


DEMO_PROVIDER = "demo"

DEMO_USERS: tuple[DemoUserSpec, ...] = (
    DemoUserSpec(
        id="11111111-1111-1111-1111-111111111111",
        email="alice@example.com",
        display_name="Alice Anderson",
    ),
    DemoUserSpec(
        id="22222222-2222-2222-2222-222222222222",
        email="bob@example.com",
        display_name="Bob Baker",
    ),
    DemoUserSpec(
        id="33333333-3333-3333-3333-333333333333",
        email="carol@example.com",
        display_name="Carol Chen",
    ),
    DemoUserSpec(
        id="44444444-4444-4444-4444-444444444444",
        email="dave@example.com",
        display_name="Dave Diaz",
        status=UserStatus.SUSPENDED,
    ),
)


def seed_demo_users(session: Session) -> list[User]:
    """Insert the demo users if absent and return the full set."""
    users_repo = SqlAlchemyUserRepository(session)
    identity_repo = SqlAlchemyExternalIdentityRepository(session)
    seeded: list[User] = []

    for spec in DEMO_USERS:
        user = users_repo.get_by_email(Email(spec.email))
        if user is None:
            user = User(
                id=UserId(spec.id),
                email=Email(spec.email),
                display_name=spec.display_name,
                status=spec.status,
            )
            users_repo.add(user)
            session.flush()
            identity_repo.add(
                ExternalIdentity.link(
                    user_id=user.id,
                    provider=DEMO_PROVIDER,
                    subject=str(user.id),
                )
            )
        seeded.append(user)

    session.flush()
    return seeded


def clear_users(session: Session) -> None:
    """Remove all users (and, by cascade, their notes and permissions)."""
    session.query(UserRow).delete()


__all__ = ["DEMO_PROVIDER", "DEMO_USERS", "DemoUserSpec", "clear_users", "seed_demo_users"]
