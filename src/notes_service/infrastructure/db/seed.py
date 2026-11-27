"""Database seeding for demo users and a realistic demo dataset.

Two levels of seeding are provided:

* :func:`seed_demo_users` — the demo *users* only. This is the stable, minimal
  primitive the test suite relies on for a known set of actors.
* :func:`seed_demo_data` — the users **plus** a full, realistic graph of folders,
  notes, sharing grants, and stars generated deterministically by
  :mod:`notes_service.infrastructure.db.demo_content`. This is what makes a
  freshly started service look like a real multi-team deployment.

Both are **idempotent**: users are matched by email and every generated row uses
a deterministic (``uuid5``) primary key, so running the seed twice is a no-op and
never duplicates data. Seeding is additive — it never deletes rows a user
created through the API.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from notes_service.application.ports.identity import DEMO_PROVIDER_NAME
from notes_service.domain.identity.entities import ExternalIdentity, User
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.db.demo_content import (
    DEMO_TEAMS,
    DEMO_USERS,
    DemoDataset,
    DemoTeam,
    DemoUserSpec,
    build_demo_dataset,
)
from notes_service.infrastructure.db.models import UserRow
from notes_service.infrastructure.db.repositories import (
    SqlAlchemyExternalIdentityRepository,
    SqlAlchemyFolderRepository,
    SqlAlchemyNotePermissionRepository,
    SqlAlchemyNoteRepository,
    SqlAlchemyNoteStarRepository,
    SqlAlchemyUserRepository,
)

DEMO_PROVIDER = DEMO_PROVIDER_NAME


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


def seed_demo_data(session: Session, *, now: datetime | None = None) -> DemoDataset:
    """Seed the demo users and their generated folders/notes/shares/stars.

    Returns the generated dataset. Idempotent: existing rows (matched by their
    deterministic primary key) are left untouched, so this is safe to run on
    every container start.
    """
    seed_demo_users(session)

    dataset = build_demo_dataset(now=now or datetime.now(UTC))

    folders_repo = SqlAlchemyFolderRepository(session)
    notes_repo = SqlAlchemyNoteRepository(session)
    permissions_repo = SqlAlchemyNotePermissionRepository(session)
    stars_repo = SqlAlchemyNoteStarRepository(session)

    for folder in dataset.folders:
        if folders_repo.get(folder.id) is None:
            folders_repo.add(folder)

    for note in dataset.notes:
        if notes_repo.get(note.id) is None:
            notes_repo.add(note)

    for permission in dataset.permissions:
        permissions_repo.upsert(permission)

    for star in dataset.stars:
        stars_repo.add(star.note_id, star.user_id)

    session.flush()
    return dataset


def clear_users(session: Session) -> None:
    """Remove all users (and, by cascade, their notes and permissions)."""
    session.query(UserRow).delete()


__all__ = [
    "DEMO_PROVIDER",
    "DEMO_TEAMS",
    "DEMO_USERS",
    "DemoDataset",
    "DemoTeam",
    "DemoUserSpec",
    "clear_users",
    "seed_demo_data",
    "seed_demo_users",
]
