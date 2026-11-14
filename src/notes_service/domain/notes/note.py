"""The :class:`Note` aggregate root.

A note is owned by exactly one user and is private by default. Sharing is
expressed through a collection of :class:`NotePermission` grants that is
managed exclusively through this aggregate, so the sharing invariants are
enforced in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from notes_service.domain.errors import InvariantViolation, ValidationError, VersionConflict
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.notes.permission import NotePermission, NotePermissionId
from notes_service.domain.notes.value_objects import (
    FolderId,
    NoteContent,
    NoteId,
    NoteTitle,
    NoteVersion,
    PermissionLevel,
)

MAX_PERMISSIONS_PER_NOTE = 100


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _initial_version() -> NoteVersion:
    return NoteVersion(1)


@dataclass(slots=True)
class Note:
    """Aggregate root for a note and its sharing grants.

    Invariants enforced here:

    * exactly one owner, fixed at creation;
    * the owner can never hold a :class:`NotePermission` (they are implicit);
    * a user holds at most one permission, and ``WRITE`` supersedes ``READ``;
    * only the owner may mutate content or the permission set.
    """

    id: NoteId
    owner_id: UserId
    title: NoteTitle
    content: NoteContent
    version: NoteVersion = field(default_factory=_initial_version)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    permissions: list[NotePermission] = field(default_factory=list)
    # The folder this note lives in, or ``None`` for the owner's root ("My Drive").
    folder_id: FolderId | None = None
    # Whether the *requesting* user has starred this note. This is a projection
    # filled by the repository for the caller, not an intrinsic note property:
    # stars are personal and must never leak between collaborators.
    is_starred: bool = False

    # -- construction -----------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        owner_id: UserId,
        title: str,
        content: str,
        note_id: NoteId | None = None,
        folder_id: FolderId | None = None,
    ) -> Note:
        """Create a new, private note owned by ``owner_id``."""
        return cls(
            id=note_id or NoteId.new(),
            owner_id=owner_id,
            title=NoteTitle(title),
            content=NoteContent(content),
            folder_id=folder_id,
        )

    # -- queries ----------------------------------------------------------

    def is_owned_by(self, user_id: UserId) -> bool:
        return self.owner_id == user_id

    def permission_for(self, user_id: UserId) -> NotePermission | None:
        for permission in self.permissions:
            if permission.user_id == user_id:
                return permission
        return None

    def effective_level(self, user_id: UserId) -> PermissionLevel | None:
        """The highest level ``user_id`` holds, or ``None`` if no access."""
        if self.is_owned_by(user_id):
            return PermissionLevel.WRITE
        permission = self.permission_for(user_id)
        return permission.level if permission else None

    def can_read(self, user_id: UserId) -> bool:
        level = self.effective_level(user_id)
        return level is not None and level.includes(PermissionLevel.READ)

    def can_write(self, user_id: UserId) -> bool:
        level = self.effective_level(user_id)
        return level is not None and level.includes(PermissionLevel.WRITE)

    def can_manage(self, user_id: UserId) -> bool:
        """Only the owner may manage sharing or delete the note."""
        return self.is_owned_by(user_id)

    def shared_user_ids(self) -> list[UserId]:
        return [permission.user_id for permission in self.permissions]

    # -- commands ---------------------------------------------------------

    def update_content(
        self,
        *,
        actor_id: UserId,
        title: str,
        content: str,
        expected_version: NoteVersion,
        now: datetime | None = None,
    ) -> None:
        """Replace title/content, enforcing ownership and optimistic concurrency."""
        if not self.can_write(actor_id):
            raise InvariantViolation("only the owner or a WRITE collaborator may update a note")
        self._assert_version(expected_version)
        self.title = NoteTitle(title)
        self.content = NoteContent(content)
        self._bump(now)

    def move_to_folder(
        self,
        *,
        actor_id: UserId,
        folder_id: FolderId | None,
        now: datetime | None = None,
    ) -> None:
        """Move this note into ``folder_id`` (or to the root when ``None``).

        Only the owner may reorganise a note: a collaborator must never be able
        to change where the owner's note lives. Moving does **not** touch the
        permission set, so sharing is unaffected by a move. Because the note's
        *location* is not its *content*, this does not advance the content
        version used for optimistic concurrency — but it does refresh
        ``updated_at``.
        """
        self.assert_owned_by(actor_id)
        self.folder_id = folder_id
        self._touch(now)

    def grant_permission(
        self,
        *,
        actor_id: UserId,
        target_user_id: UserId,
        level: PermissionLevel,
        permission_id: NotePermissionId | None = None,
        now: datetime | None = None,
    ) -> NotePermission:
        """Grant or upgrade ``target_user_id``'s access. Owner-only."""
        if not self.is_owned_by(actor_id):
            raise InvariantViolation("only the owner may grant permissions")
        if target_user_id == self.owner_id:
            raise InvariantViolation("cannot grant a permission to the note owner")

        existing = self.permission_for(target_user_id)
        if existing is not None:
            existing.upgrade_to(level)
            existing.granted_by = actor_id
            self._touch(now)
            return existing

        if len(self.permissions) >= MAX_PERMISSIONS_PER_NOTE:
            raise ValidationError(
                f"a note may have at most {MAX_PERMISSIONS_PER_NOTE} permission grants"
            )
        permission = NotePermission(
            id=permission_id or NotePermissionId.new(),
            note_id=self.id,
            user_id=target_user_id,
            level=level,
            granted_by=actor_id,
        )
        self.permissions.append(permission)
        self._touch(now)
        return permission

    def revoke_permission(
        self, *, actor_id: UserId, target_user_id: UserId, now: datetime | None = None
    ) -> bool:
        """Remove ``target_user_id``'s access. Owner-only.

        Returns ``True`` if a grant existed and was removed, ``False`` if there
        was nothing to remove (the caller decides whether that is a 404).
        """
        if not self.is_owned_by(actor_id):
            raise InvariantViolation("only the owner may revoke permissions")
        existing = self.permission_for(target_user_id)
        if existing is None:
            return False
        self.permissions.remove(existing)
        self._touch(now)
        return True

    def assert_owned_by(self, actor_id: UserId) -> None:
        """Raise unless ``actor_id`` owns this note."""
        if not self.is_owned_by(actor_id):
            raise InvariantViolation("operation requires note ownership")

    # -- internals --------------------------------------------------------

    def _assert_version(self, expected: NoteVersion) -> None:
        if expected != self.version:
            raise VersionConflict(expected=int(expected), actual=int(self.version))

    def _bump(self, now: datetime | None) -> None:
        self.version = self.version.next()
        self.updated_at = now or _utcnow()

    def _touch(self, now: datetime | None) -> None:
        """Record a sharing change without advancing the content version.

        Sharing changes are part of the aggregate but are not content revisions,
        so the note ``version`` (used for optimistic concurrency on content)
        stays put while ``updated_at`` reflects the change.
        """
        self.updated_at = now or _utcnow()
