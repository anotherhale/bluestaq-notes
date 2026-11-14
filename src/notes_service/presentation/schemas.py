"""Pydantic v2 request and response schemas.

These schemas are the public contract. They are intentionally separate from both
the domain entities and the ORM rows so that neither leaks across the HTTP
boundary. ``model_config`` uses ``extra="forbid"`` on request bodies so that
unexpected fields are rejected rather than silently ignored.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from notes_service.domain.identity.entities import User
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.notes.folder import Folder
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.permission import NotePermission
from notes_service.domain.notes.value_objects import (
    MAX_CONTENT_BYTES,
    MAX_FOLDER_NAME_LENGTH,
    MAX_TITLE_LENGTH,
    PermissionLevel,
)


class StrictModel(BaseModel):
    """Base for request models: reject unknown fields."""

    model_config = ConfigDict(extra="forbid")


class ResponseModel(BaseModel):
    """Base for response models: serialise from domain objects."""

    model_config = ConfigDict(from_attributes=True)


# -- identity -------------------------------------------------------------


class UserResponse(ResponseModel):
    """A user as exposed for sharing. Only sharing-relevant fields are returned."""

    id: uuid.UUID
    email: str
    display_name: str

    @classmethod
    def from_domain(cls, user: User) -> UserResponse:
        return cls(
            id=uuid.UUID(str(user.id)), email=str(user.email), display_name=user.display_name
        )


class CurrentUserResponse(ResponseModel):
    """The authenticated caller. Includes status, which is safe for self."""

    id: uuid.UUID
    email: str
    display_name: str
    status: str

    @classmethod
    def from_domain(cls, user: User) -> CurrentUserResponse:
        return cls(
            id=uuid.UUID(str(user.id)),
            email=str(user.email),
            display_name=user.display_name,
            status=user.status.value,
        )


# -- shared ---------------------------------------------------------------


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


# -- notes ----------------------------------------------------------------


class NoteCreateRequest(StrictModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    content: str = Field(default="", max_length=MAX_CONTENT_BYTES)
    # Optional placement at creation; omitted means the owner's root.
    folder_id: uuid.UUID | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class NoteUpdateRequest(StrictModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    content: str = Field(default="", max_length=MAX_CONTENT_BYTES)
    # Optimistic concurrency: the client must state the version it edited.
    version: int = Field(ge=1)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class NoteMoveRequest(StrictModel):
    """Move a note into a folder (``folder_id``) or to the root (``null``)."""

    folder_id: uuid.UUID | None = None


class NoteResponse(ResponseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    content: str
    version: int
    folder_id: uuid.UUID | None
    is_starred: bool
    # The caller's *own* effective level. Exposing only the caller's level (not
    # the full collaborator list) is what lets a collaborator know they can edit
    # without leaking who else the note is shared with.
    caller_permission: PermissionLevel | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, note: Note, *, caller_id: uuid.UUID) -> NoteResponse:
        return cls(
            id=uuid.UUID(str(note.id)),
            owner_id=uuid.UUID(str(note.owner_id)),
            title=str(note.title),
            content=str(note.content),
            version=int(note.version),
            folder_id=uuid.UUID(str(note.folder_id)) if note.folder_id is not None else None,
            is_starred=note.is_starred,
            caller_permission=note.effective_level(UserId(str(caller_id))),
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


class NoteSummaryResponse(ResponseModel):
    """A note without its content, for list endpoints."""

    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    version: int
    folder_id: uuid.UUID | None
    is_starred: bool
    caller_permission: PermissionLevel | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, note: Note, *, caller_id: uuid.UUID) -> NoteSummaryResponse:
        return cls(
            id=uuid.UUID(str(note.id)),
            owner_id=uuid.UUID(str(note.owner_id)),
            title=str(note.title),
            version=int(note.version),
            folder_id=uuid.UUID(str(note.folder_id)) if note.folder_id is not None else None,
            is_starred=note.is_starred,
            caller_permission=note.effective_level(UserId(str(caller_id))),
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


# -- folders --------------------------------------------------------------


class FolderCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=MAX_FOLDER_NAME_LENGTH)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


class FolderRenameRequest(StrictModel):
    name: str = Field(min_length=1, max_length=MAX_FOLDER_NAME_LENGTH)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


class FolderResponse(ResponseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, folder: Folder) -> FolderResponse:
        return cls(
            id=uuid.UUID(str(folder.id)),
            owner_id=uuid.UUID(str(folder.owner_id)),
            name=str(folder.name),
            created_at=folder.created_at,
            updated_at=folder.updated_at,
        )


class FolderListResponse(BaseModel):
    items: list[FolderResponse]
    meta: PageMeta


# -- permissions ----------------------------------------------------------


class PermissionGrantRequest(StrictModel):
    user_id: uuid.UUID
    permission: PermissionLevel

    @field_validator("permission", mode="before")
    @classmethod
    def _normalise_permission(cls, value: object) -> object:
        if isinstance(value, str):
            return value.lower()
        return value


class PermissionResponse(ResponseModel):
    user_id: uuid.UUID
    permission: PermissionLevel
    granted_by: uuid.UUID
    created_at: datetime

    @classmethod
    def from_domain(cls, permission: NotePermission) -> PermissionResponse:
        return cls(
            user_id=uuid.UUID(str(permission.user_id)),
            permission=permission.level,
            granted_by=uuid.UUID(str(permission.granted_by)),
            created_at=permission.created_at,
        )


# -- envelopes ------------------------------------------------------------


class NoteListResponse(BaseModel):
    items: list[NoteSummaryResponse]
    meta: PageMeta


class UserListResponse(BaseModel):
    items: list[UserResponse]
    meta: PageMeta


class DemoIdentityResponse(ResponseModel):
    """A selectable development demo identity.

    Deliberately the same shape as :class:`UserResponse` (id, email, display
    name) so the client can treat a demo identity and a shareable user
    uniformly. It exposes nothing beyond what is needed to pick an identity.
    """

    id: uuid.UUID
    email: str
    display_name: str

    @classmethod
    def from_domain(cls, user: User) -> DemoIdentityResponse:
        return cls(
            id=uuid.UUID(str(user.id)), email=str(user.email), display_name=user.display_name
        )


class DemoIdentityListResponse(BaseModel):
    """The set of demo identities a client may select, with a header hint.

    ``header`` tells the client exactly which request header selects an
    identity, so the frontend never hardcodes it.
    """

    enabled: bool
    header: str
    items: list[DemoIdentityResponse]
    meta: PageMeta


class PermissionListResponse(BaseModel):
    items: list[PermissionResponse]
    meta: PageMeta


class ErrorBody(BaseModel):
    """A single error object inside the standard error envelope."""

    code: str
    message: str
    details: list[dict[str, object]] | None = None


class ErrorResponse(BaseModel):
    """The standard error envelope returned for every failure."""

    error: ErrorBody
    request_id: str | None = None
