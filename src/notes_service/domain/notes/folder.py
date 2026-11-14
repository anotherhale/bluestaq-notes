"""The :class:`Folder` aggregate root.

Folders are a *flat, owner-scoped* organisation aid: a folder belongs to exactly
one user and a note belongs to at most one folder. There is deliberately no
parent/child hierarchy — the product need is "group my notes", and a flat model
keeps the move/delete semantics unambiguous (see ADR-009).

Folders are **not** a sharing boundary. Sharing a note never grants access to
the folder that contains it, and folder membership is only ever changed by the
note's owner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from notes_service.domain.errors import InvariantViolation
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.notes.value_objects import FolderId, FolderName


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Folder:
    """A named container for one user's notes."""

    id: FolderId
    owner_id: UserId
    name: FolderName
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def create(
        cls,
        *,
        owner_id: UserId,
        name: str,
        folder_id: FolderId | None = None,
    ) -> Folder:
        return cls(id=folder_id or FolderId.new(), owner_id=owner_id, name=FolderName(name))

    def is_owned_by(self, user_id: UserId) -> bool:
        return self.owner_id == user_id

    def assert_owned_by(self, actor_id: UserId) -> None:
        if not self.is_owned_by(actor_id):
            raise InvariantViolation("operation requires folder ownership")

    def rename(self, *, actor_id: UserId, name: str, now: datetime | None = None) -> None:
        self.assert_owned_by(actor_id)
        self.name = FolderName(name)
        self.updated_at = now or _utcnow()
