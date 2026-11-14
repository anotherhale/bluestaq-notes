"""Notes domain context: the :class:`Note` aggregate and its value objects."""

from notes_service.domain.notes.note import Note
from notes_service.domain.notes.permission import NotePermission
from notes_service.domain.notes.value_objects import (
    MAX_CONTENT_BYTES,
    MAX_TITLE_LENGTH,
    NoteContent,
    NoteId,
    NoteTitle,
    NoteVersion,
    PermissionLevel,
)

__all__ = [
    "MAX_CONTENT_BYTES",
    "MAX_TITLE_LENGTH",
    "Note",
    "NoteContent",
    "NoteId",
    "NotePermission",
    "NoteTitle",
    "NoteVersion",
    "PermissionLevel",
]
