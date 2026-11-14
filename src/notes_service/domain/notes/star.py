"""The :class:`NoteStar` entity: a per-user "starred" marker on a note.

Starring is **personal**: a star belongs to the user who set it and never
changes what any other collaborator sees. It is therefore a separate
relationship rather than a field on the note, mirroring how a "favourite" works
in a shared document product.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.notes.value_objects import NoteId


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class NoteStar:
    """A ``(note, user)`` pair recording that ``user_id`` starred ``note_id``."""

    note_id: NoteId
    user_id: UserId
    created_at: datetime = field(default_factory=_utcnow)


__all__ = ["NoteStar"]
