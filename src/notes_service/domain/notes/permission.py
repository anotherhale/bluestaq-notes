"""The :class:`NotePermission` entity: a grant of access to a shared user."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from notes_service.domain.errors import InvariantViolation
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.notes.value_objects import NoteId, PermissionLevel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NotePermissionId(uuid.UUID):
    """Strongly-typed identifier for a permission grant."""

    __slots__ = ()

    @classmethod
    def new(cls) -> NotePermissionId:
        return cls(str(uuid.uuid4()))


@dataclass(slots=True)
class NotePermission:
    """A single ``(note, user) -> level`` grant.

    A permission always names the user who granted it (``granted_by``) for audit
    purposes. The owner is implicitly fully authorised and is never represented
    by a ``NotePermission`` row.
    """

    id: NotePermissionId
    note_id: NoteId
    user_id: UserId
    level: PermissionLevel
    granted_by: UserId
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if self.user_id == self.granted_by:
            raise InvariantViolation("a user cannot grant a permission to themselves")

    def upgrade_to(self, level: PermissionLevel) -> None:
        """Replace this grant's level (used when re-sharing at a higher level)."""
        self.level = level
