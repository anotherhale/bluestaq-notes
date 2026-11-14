"""Application use-case tests.

Use cases are exercised directly (not over HTTP) to prove the application layer
enforces authorization and transaction semantics independently of FastAPI.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import AccessDenied, Conflict, InvalidInput, ResourceNotFound
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.identity import GetCurrentUser, ListUsers
from notes_service.application.use_cases.notes import (
    CreateNote,
    DeleteNote,
    GetNote,
    ListNotes,
    UpdateNote,
)
from notes_service.application.use_cases.permissions import (
    ListPermissions,
    RevokePermission,
    ShareNote,
)
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.domain.notes.value_objects import NoteVersion, PermissionLevel
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.security.clock import SystemClock

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
BOB = UserId(DEMO_USERS[1].id)
CAROL = UserId(DEMO_USERS[2].id)


def principal_for(user_id: UserId, email: str = "x@example.com") -> Principal:
    return Principal(
        user_id=user_id, email=Email(email), display_name="X", provider="demo", subject=str(user_id)
    )


@pytest.fixture
def use_cases(session_factory: sessionmaker[Session]) -> dict[str, object]:
    auth = DefaultAuthorizationProvider()
    clock = SystemClock()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    def make(cls):  # type: ignore[no-untyped-def]
        return cls(uow_factory=uow_factory, authorization=auth, clock=clock)

    return {
        "create": make(CreateNote),
        "get": make(GetNote),
        "list": make(ListNotes),
        "update": make(UpdateNote),
        "delete": make(DeleteNote),
        "share": make(ShareNote),
        "revoke": make(RevokePermission),
        "list_perms": make(ListPermissions),
        "me": make(GetCurrentUser),
        "users": make(ListUsers),
    }


class TestNoteLifecycle:
    def test_create_assigns_ownership_from_principal(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        note = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        assert note.owner_id == ALICE
        assert note.version == 1

    def test_get_returns_note_for_owner(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        created = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        fetched = use_cases["get"].run(principal_for(ALICE), note_id=created.id)
        assert fetched.id == created.id

    def test_get_hides_other_users_note(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        created = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        with pytest.raises(ResourceNotFound):
            use_cases["get"].run(principal_for(BOB), note_id=created.id)

    def test_update_bumps_version(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        created = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        updated = use_cases["update"].run(
            principal_for(ALICE),
            note_id=created.id,
            title="T2",
            content="C2",
            expected_version=NoteVersion(1),
        )
        assert updated.version == 2

    def test_update_with_stale_version_conflicts(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        created = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        use_cases["update"].run(
            principal_for(ALICE),
            note_id=created.id,
            title="T2",
            content="C2",
            expected_version=NoteVersion(1),
        )
        with pytest.raises(Conflict):
            use_cases["update"].run(
                principal_for(ALICE),
                note_id=created.id,
                title="T3",
                content="C3",
                expected_version=NoteVersion(1),
            )

    def test_delete_owner_only(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        created = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        with pytest.raises(ResourceNotFound):
            use_cases["delete"].run(principal_for(BOB), note_id=created.id)
        use_cases["delete"].run(principal_for(ALICE), note_id=created.id)
        with pytest.raises(ResourceNotFound):
            use_cases["get"].run(principal_for(ALICE), note_id=created.id)


class TestSharing:
    def test_share_then_read(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        note = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        use_cases["share"].run(
            principal_for(ALICE),
            note_id=note.id,
            target_user_id=BOB,
            level=PermissionLevel.READ,
        )
        fetched = use_cases["get"].run(principal_for(BOB), note_id=note.id)
        assert fetched.id == note.id

    def test_write_collaborator_can_update(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        note = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        use_cases["share"].run(
            principal_for(ALICE),
            note_id=note.id,
            target_user_id=BOB,
            level=PermissionLevel.WRITE,
        )
        updated = use_cases["update"].run(
            principal_for(BOB),
            note_id=note.id,
            title="by bob",
            content="",
            expected_version=NoteVersion(1),
        )
        assert updated.version == 2

    def test_write_collaborator_cannot_delete(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        note = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        use_cases["share"].run(
            principal_for(ALICE),
            note_id=note.id,
            target_user_id=BOB,
            level=PermissionLevel.WRITE,
        )
        with pytest.raises(AccessDenied):
            use_cases["delete"].run(principal_for(BOB), note_id=note.id)

    def test_read_collaborator_cannot_update(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        note = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        use_cases["share"].run(
            principal_for(ALICE),
            note_id=note.id,
            target_user_id=BOB,
            level=PermissionLevel.READ,
        )
        with pytest.raises(AccessDenied):
            use_cases["update"].run(
                principal_for(BOB),
                note_id=note.id,
                title="no",
                content="",
                expected_version=NoteVersion(1),
            )

    def test_non_owner_cannot_share(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        note = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        use_cases["share"].run(
            principal_for(ALICE),
            note_id=note.id,
            target_user_id=BOB,
            level=PermissionLevel.WRITE,
        )
        with pytest.raises(AccessDenied):
            use_cases["share"].run(
                principal_for(BOB),
                note_id=note.id,
                target_user_id=CAROL,
                level=PermissionLevel.READ,
            )

    def test_cannot_share_with_nonexistent_user(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        note = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        with pytest.raises(InvalidInput):
            use_cases["share"].run(
                principal_for(ALICE),
                note_id=note.id,
                target_user_id=UserId.new(),
                level=PermissionLevel.READ,
            )

    def test_cannot_share_with_owner(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        note = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        with pytest.raises(InvalidInput):
            use_cases["share"].run(
                principal_for(ALICE),
                note_id=note.id,
                target_user_id=ALICE,
                level=PermissionLevel.READ,
            )

    def test_revoke_removes_access(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        note = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        use_cases["share"].run(
            principal_for(ALICE), note_id=note.id, target_user_id=BOB, level=PermissionLevel.READ
        )
        use_cases["revoke"].run(principal_for(ALICE), note_id=note.id, target_user_id=BOB)
        with pytest.raises(ResourceNotFound):
            use_cases["get"].run(principal_for(BOB), note_id=note.id)

    def test_revoke_unknown_permission_is_not_found(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        note = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        with pytest.raises(ResourceNotFound):
            use_cases["revoke"].run(principal_for(ALICE), note_id=note.id, target_user_id=BOB)

    def test_list_permissions_owner_only(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        note = use_cases["create"].run(principal_for(ALICE), title="T", content="C")
        use_cases["share"].run(
            principal_for(ALICE), note_id=note.id, target_user_id=BOB, level=PermissionLevel.WRITE
        )
        perms = use_cases["list_perms"].run(principal_for(ALICE), note_id=note.id)
        assert len(perms) == 1 and perms[0].user_id == BOB
        with pytest.raises(AccessDenied):
            use_cases["list_perms"].run(principal_for(BOB), note_id=note.id)


class TestListing:
    def test_list_includes_owned_and_shared_without_duplicates(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        owned = use_cases["create"].run(principal_for(ALICE), title="owned", content="")
        other = use_cases["create"].run(principal_for(BOB), title="shared", content="")
        use_cases["share"].run(
            principal_for(BOB), note_id=other.id, target_user_id=ALICE, level=PermissionLevel.READ
        )
        page = use_cases["list"].run(principal_for(ALICE), limit=50, offset=0)
        ids = [note.id for note in page.items]
        assert owned.id in ids and other.id in ids
        assert len(ids) == len(set(ids))
        assert page.total == 2

    def test_pagination_offset(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        for index in range(5):
            use_cases["create"].run(principal_for(ALICE), title=f"n{index}", content="")
        page = use_cases["list"].run(principal_for(ALICE), limit=2, offset=0)
        assert len(page.items) == 2
        assert page.total == 5


class TestIdentityUseCases:
    def test_get_current_user(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        user = use_cases["me"].run(principal_for(ALICE, "alice@example.com"))
        assert str(user.id) == str(ALICE)

    def test_list_users_returns_seeded(self, use_cases) -> None:  # type: ignore[no-untyped-def]
        users = use_cases["users"].run(principal_for(ALICE), limit=50, offset=0)
        emails = {str(user.email) for user in users}
        assert "alice@example.com" in emails
        assert use_cases["users"].count(principal_for(ALICE)) >= 4
