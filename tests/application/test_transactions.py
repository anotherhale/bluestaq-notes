"""Tests for transaction rollback behaviour at the use-case level."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import InvalidInput
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.notes import CreateNote
from notes_service.application.use_cases.permissions import ShareNote
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.domain.notes.value_objects import PermissionLevel
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.security.clock import SystemClock

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)


def principal() -> Principal:
    return Principal(
        user_id=ALICE,
        email=Email("alice@example.com"),
        display_name="Alice",
        provider="demo",
        subject=str(ALICE),
    )


def test_failed_share_does_not_persist_partial_state(
    session_factory: sessionmaker[Session],
) -> None:
    """A share that fails validation must not leave a permission row behind."""
    auth = DefaultAuthorizationProvider()
    clock = SystemClock()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    create = CreateNote(uow_factory=uow_factory, authorization=auth, clock=clock)
    share = ShareNote(uow_factory=uow_factory, authorization=auth, clock=clock)

    note = create.run(principal(), title="T", content="C")

    # Sharing with a nonexistent user raises before any write is committed.
    with pytest.raises(InvalidInput):
        share.run(
            principal(),
            note_id=note.id,
            target_user_id=UserId.new(),
            level=PermissionLevel.READ,
        )

    # The note exists with no permissions and an unchanged version.
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        loaded = uow.notes.get(note.id)
        assert loaded is not None
        assert loaded.permissions == []
        assert loaded.version == 1
