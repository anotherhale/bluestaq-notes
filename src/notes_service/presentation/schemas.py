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
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.permission import NotePermission
from notes_service.domain.notes.value_objects import (
    MAX_CONTENT_BYTES,
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


# -- notes ----------------------------------------------------------------


class NoteCreateRequest(StrictModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    content: str = Field(default="", max_length=MAX_CONTENT_BYTES)

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


class NoteResponse(ResponseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    content: str
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, note: Note) -> NoteResponse:
        return cls(
            id=uuid.UUID(str(note.id)),
            owner_id=uuid.UUID(str(note.owner_id)),
            title=str(note.title),
            content=str(note.content),
            version=int(note.version),
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


class NoteSummaryResponse(ResponseModel):
    """A note without its content, for list endpoints."""

    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, note: Note) -> NoteSummaryResponse:
        return cls(
            id=uuid.UUID(str(note.id)),
            owner_id=uuid.UUID(str(note.owner_id)),
            title=str(note.title),
            version=int(note.version),
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


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


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class NoteListResponse(BaseModel):
    items: list[NoteSummaryResponse]
    meta: PageMeta


class UserListResponse(BaseModel):
    items: list[UserResponse]
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
