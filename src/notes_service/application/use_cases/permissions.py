"""Permission management use cases: share, revoke, list."""

from __future__ import annotations

from notes_service.application.errors import InvalidInput, ResourceNotFound
from notes_service.application.ports.authorization import Action
from notes_service.application.use_cases.base import UseCase
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.notes.permission import NotePermission
from notes_service.domain.notes.value_objects import NoteId, PermissionLevel


class ShareNote(UseCase):
    """Grant or upgrade another user's access to a note. Owner only.

    Re-sharing an existing collaborator at a higher level upgrades the grant
    rather than creating a duplicate.
    """

    def run(
        self,
        principal: Principal,
        *,
        note_id: NoteId,
        target_user_id: UserId,
        level: PermissionLevel,
    ) -> NotePermission:
        with self._uow_factory() as uow:
            note = self._load_note_for_action(
                uow,
                principal=principal,
                note_id=note_id,
                action=Action.MANAGE_PERMISSIONS,
                denied_message="only the note owner may manage permissions",
            )

            if target_user_id == note.owner_id:
                raise InvalidInput("the note owner already has full access")

            # The target must be a real, active user: we never grant access to a
            # user id that does not resolve.
            target = uow.users.get(target_user_id)
            if target is None or not target.is_active:
                raise InvalidInput("target user does not exist or is not active")

            permission = note.grant_permission(
                actor_id=principal.user_id,
                target_user_id=target_user_id,
                level=level,
                now=self._clock.now(),
            )
            uow.permissions.upsert(permission)
            uow.commit()
            return permission


class RevokePermission(UseCase):
    """Revoke another user's access to a note. Owner only."""

    def run(self, principal: Principal, *, note_id: NoteId, target_user_id: UserId) -> None:
        with self._uow_factory() as uow:
            note = self._load_note_for_action(
                uow,
                principal=principal,
                note_id=note_id,
                action=Action.MANAGE_PERMISSIONS,
                denied_message="only the note owner may manage permissions",
            )

            removed = note.revoke_permission(
                actor_id=principal.user_id,
                target_user_id=target_user_id,
                now=self._clock.now(),
            )
            if not removed:
                # Nothing to revoke: report 404 so the outcome is unambiguous.
                raise ResourceNotFound("permission not found")

            uow.permissions.remove(note_id, target_user_id)
            uow.commit()


class ListPermissions(UseCase):
    """List the collaborators on a note. Owner only.

    Non-owners (including WRITE collaborators) cannot enumerate the share list,
    which avoids leaking the membership of a shared note.
    """

    def run(self, principal: Principal, *, note_id: NoteId) -> list[NotePermission]:
        with self._uow_factory() as uow:
            self._load_note_for_action(
                uow,
                principal=principal,
                note_id=note_id,
                action=Action.MANAGE_PERMISSIONS,
                denied_message="only the note owner may view permissions",
            )
            return uow.permissions.list_for_note(note_id)
