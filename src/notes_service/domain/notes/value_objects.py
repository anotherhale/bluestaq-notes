"""Notes value objects: identifiers, content, version, permission level."""

from __future__ import annotations

import uuid
from enum import StrEnum

from notes_service.domain.errors import ValidationError
from notes_service.domain.notes.rich_text import sanitize_html

MAX_TITLE_LENGTH = 200
MAX_FOLDER_NAME_LENGTH = 100
MAX_CONTENT_BYTES = 64 * 1024  # 64 KiB of UTF-8 content


class NoteId(uuid.UUID):
    """Strongly-typed note identifier."""

    __slots__ = ()

    @classmethod
    def new(cls) -> NoteId:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str | uuid.UUID) -> NoteId:
        if isinstance(value, NoteId):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid note id: {value!r}") from exc


class FolderId(uuid.UUID):
    """Strongly-typed folder identifier."""

    __slots__ = ()

    @classmethod
    def new(cls) -> FolderId:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str | uuid.UUID) -> FolderId:
        if isinstance(value, FolderId):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid folder id: {value!r}") from exc


class FolderName(str):
    """A validated, normalised folder name."""

    __slots__ = ()

    def __new__(cls, value: str) -> FolderName:
        if not isinstance(value, str):
            raise ValidationError("folder name must be a string")
        candidate = value.strip()
        if not candidate:
            raise ValidationError("folder name must not be empty")
        if len(candidate) > MAX_FOLDER_NAME_LENGTH:
            raise ValidationError(
                f"folder name must be at most {MAX_FOLDER_NAME_LENGTH} characters"
            )
        if "\x00" in candidate:
            raise ValidationError("folder name must not contain NUL characters")
        return super().__new__(cls, candidate)


class NoteTitle(str):
    """A validated, normalised note title."""

    __slots__ = ()

    def __new__(cls, value: str) -> NoteTitle:
        if not isinstance(value, str):
            raise ValidationError("title must be a string")
        candidate = value.strip()
        if not candidate:
            raise ValidationError("title must not be empty")
        if len(candidate) > MAX_TITLE_LENGTH:
            raise ValidationError(f"title must be at most {MAX_TITLE_LENGTH} characters")
        if "\x00" in candidate:
            raise ValidationError("title must not contain NUL characters")
        return super().__new__(cls, candidate)


class NoteContent(str):
    """Validated, sanitised note body.

    The body is HTML (the WYSIWYG editor's output). Every value is run through
    :func:`sanitize_html` on construction, so a ``NoteContent`` can never hold a
    script, an iframe, an event handler, or an unsafe URL. Content may be empty
    but is bounded by size to protect the service from unbounded payloads even
    before the HTTP body limit is applied.

    Plain text remains valid input (it is simply HTML with no markup), so the
    switch from plain text to HTML is backward compatible.
    """

    __slots__ = ()

    def __new__(cls, value: str) -> NoteContent:
        if not isinstance(value, str):
            raise ValidationError("content must be a string")
        if "\x00" in value:
            raise ValidationError("content must not contain NUL characters")
        cleaned = sanitize_html(value)
        if len(cleaned.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise ValidationError(f"content must be at most {MAX_CONTENT_BYTES} bytes of UTF-8")
        return super().__new__(cls, cleaned)


class NoteVersion(int):
    """A monotonically increasing note revision number.

    Versions start at ``1`` and increase by one on every accepted mutation. The
    version is the basis of optimistic concurrency control.
    """

    __slots__ = ()

    def __new__(cls, value: int) -> NoteVersion:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError("version must be an integer")
        if value < 1:
            raise ValidationError("version must be >= 1")
        return super().__new__(cls, value)

    def next(self) -> NoteVersion:
        return NoteVersion(int(self) + 1)


class PermissionLevel(StrEnum):
    """Level of access granted to a shared user.

    Ordered from least to most privileged. ``WRITE`` implies ``READ``; the
    domain never stores an explicit ``READ`` row alongside a ``WRITE`` row for
    the same (note, user) pair.
    """

    READ = "read"
    WRITE = "write"

    @property
    def rank(self) -> int:
        return 1 if self is PermissionLevel.READ else 2

    def includes(self, other: PermissionLevel) -> bool:
        """True if holding ``self`` implies holding ``other``."""
        return self.rank >= other.rank

    @classmethod
    def parse(cls, value: str) -> PermissionLevel:
        try:
            return cls(value.lower())
        except (ValueError, AttributeError) as exc:
            allowed = ", ".join(member.value for member in cls)
            raise ValidationError(
                f"invalid permission level {value!r}; expected one of: {allowed}"
            ) from exc
