"""Domain unit tests for the Note aggregate and its value objects."""

from __future__ import annotations

import pytest

from notes_service.domain.errors import InvariantViolation, ValidationError, VersionConflict
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.notes.note import MAX_PERMISSIONS_PER_NOTE, Note
from notes_service.domain.notes.value_objects import (
    MAX_TITLE_LENGTH,
    NoteContent,
    NoteTitle,
    NoteVersion,
    PermissionLevel,
)

OWNER = UserId("11111111-1111-1111-1111-111111111111")
OTHER = UserId("22222222-2222-2222-2222-222222222222")
THIRD = UserId("33333333-3333-3333-3333-333333333333")


def make_note() -> Note:
    return Note.create(owner_id=OWNER, title="Title", content="Body")


class TestNoteCreation:
    def test_created_private_with_version_one(self) -> None:
        note = make_note()
        assert note.version == 1
        assert note.permissions == []
        assert note.is_owned_by(OWNER)
        assert not note.can_read(OTHER)

    def test_title_is_trimmed(self) -> None:
        note = Note.create(owner_id=OWNER, title="  spaced  ", content="")
        assert note.title == "spaced"

    def test_blank_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Note.create(owner_id=OWNER, title="   ", content="")

    def test_overlong_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NoteTitle("x" * (MAX_TITLE_LENGTH + 1))

    def test_nul_in_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NoteTitle("bad\x00title")

    def test_content_size_bounded(self) -> None:
        with pytest.raises(ValidationError):
            NoteContent("a" * (64 * 1024 + 1))

    def test_empty_content_allowed(self) -> None:
        assert NoteContent("") == ""


class TestOwnershipAndPermissions:
    def test_owner_has_full_access(self) -> None:
        note = make_note()
        assert note.can_read(OWNER)
        assert note.can_write(OWNER)
        assert note.can_manage(OWNER)

    def test_grant_read(self) -> None:
        note = make_note()
        note.grant_permission(actor_id=OWNER, target_user_id=OTHER, level=PermissionLevel.READ)
        assert note.can_read(OTHER)
        assert not note.can_write(OTHER)
        assert not note.can_manage(OTHER)

    def test_grant_write_implies_read(self) -> None:
        note = make_note()
        note.grant_permission(actor_id=OWNER, target_user_id=OTHER, level=PermissionLevel.WRITE)
        assert note.can_read(OTHER)
        assert note.can_write(OTHER)
        assert not note.can_manage(OTHER)

    def test_non_owner_cannot_grant(self) -> None:
        note = make_note()
        note.grant_permission(actor_id=OWNER, target_user_id=OTHER, level=PermissionLevel.WRITE)
        with pytest.raises(InvariantViolation):
            note.grant_permission(actor_id=OTHER, target_user_id=THIRD, level=PermissionLevel.READ)

    def test_cannot_grant_to_owner(self) -> None:
        note = make_note()
        with pytest.raises(InvariantViolation):
            note.grant_permission(actor_id=OWNER, target_user_id=OWNER, level=PermissionLevel.READ)

    def test_regrant_upgrades_without_duplicate(self) -> None:
        note = make_note()
        note.grant_permission(actor_id=OWNER, target_user_id=OTHER, level=PermissionLevel.READ)
        note.grant_permission(actor_id=OWNER, target_user_id=OTHER, level=PermissionLevel.WRITE)
        assert len(note.permissions) == 1
        assert note.effective_level(OTHER) == PermissionLevel.WRITE

    def test_revoke_removes_access_immediately(self) -> None:
        note = make_note()
        note.grant_permission(actor_id=OWNER, target_user_id=OTHER, level=PermissionLevel.WRITE)
        assert note.can_read(OTHER)
        assert note.revoke_permission(actor_id=OWNER, target_user_id=OTHER) is True
        assert not note.can_read(OTHER)
        assert not note.can_write(OTHER)

    def test_revoke_unknown_returns_false(self) -> None:
        note = make_note()
        assert note.revoke_permission(actor_id=OWNER, target_user_id=OTHER) is False

    def test_non_owner_cannot_revoke(self) -> None:
        note = make_note()
        note.grant_permission(actor_id=OWNER, target_user_id=OTHER, level=PermissionLevel.WRITE)
        with pytest.raises(InvariantViolation):
            note.revoke_permission(actor_id=OTHER, target_user_id=OTHER)

    def test_permission_limit_enforced(self) -> None:
        note = make_note()
        for index in range(MAX_PERMISSIONS_PER_NOTE):
            target = UserId(f"{index:08d}-0000-0000-0000-000000000000")
            note.grant_permission(actor_id=OWNER, target_user_id=target, level=PermissionLevel.READ)
        with pytest.raises(ValidationError):
            note.grant_permission(
                actor_id=OWNER,
                target_user_id=UserId("99999999-0000-0000-0000-000000000000"),
                level=PermissionLevel.READ,
            )


class TestVersioning:
    def test_update_bumps_version(self) -> None:
        note = make_note()
        note.update_content(
            actor_id=OWNER,
            title="New",
            content="New body",
            expected_version=NoteVersion(1),
        )
        assert note.version == 2
        assert note.title == "New"

    def test_stale_update_conflicts(self) -> None:
        note = make_note()
        note.update_content(actor_id=OWNER, title="v2", content="", expected_version=NoteVersion(1))
        with pytest.raises(VersionConflict) as exc:
            note.update_content(
                actor_id=OWNER, title="v3", content="", expected_version=NoteVersion(1)
            )
        assert exc.value.expected == 1
        assert exc.value.actual == 2

    def test_writer_can_update_but_not_delete(self) -> None:
        note = make_note()
        note.grant_permission(actor_id=OWNER, target_user_id=OTHER, level=PermissionLevel.WRITE)
        note.update_content(actor_id=OTHER, title="ok", content="", expected_version=NoteVersion(1))
        assert note.version == 2
        with pytest.raises(InvariantViolation):
            note.assert_owned_by(OTHER)

    def test_read_user_cannot_update(self) -> None:
        note = make_note()
        note.grant_permission(actor_id=OWNER, target_user_id=OTHER, level=PermissionLevel.READ)
        with pytest.raises(InvariantViolation):
            note.update_content(
                actor_id=OTHER, title="no", content="", expected_version=NoteVersion(1)
            )

    def test_sharing_change_does_not_bump_content_version(self) -> None:
        note = make_note()
        note.grant_permission(actor_id=OWNER, target_user_id=OTHER, level=PermissionLevel.READ)
        assert note.version == 1


class TestPermissionLevelOrdering:
    def test_write_includes_read(self) -> None:
        assert PermissionLevel.WRITE.includes(PermissionLevel.READ)
        assert not PermissionLevel.READ.includes(PermissionLevel.WRITE)

    def test_parse_is_case_insensitive(self) -> None:
        assert PermissionLevel.parse("READ") == PermissionLevel.READ
        assert PermissionLevel.parse("write") == PermissionLevel.WRITE

    def test_parse_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            PermissionLevel.parse("admin")
