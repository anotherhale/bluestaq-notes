"""SQLAlchemy ORM models.

These models are the persistence representation only. They are never returned
from the API and never imported by the domain or application layers.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from notes_service.infrastructure.db.base import Base, UTCDateTime, UUIDType, utcnow


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("status IN ('active','suspended','disabled')", name="status_valid"),
    )


class ExternalIdentityRow(Base):
    __tablename__ = "external_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_external_identities_provider_subject"),
        Index("ix_external_identities_provider_subject", "provider", "subject"),
    )


class NoteRow(Base):
    __tablename__ = "notes"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    permissions: Mapped[list[NotePermissionRow]] = relationship(
        back_populates="note", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(title) >= 1", name="title_not_empty"),
        Index("ix_notes_owner_id_updated_at", "owner_id", "updated_at"),
    )


class NotePermissionRow(Base):
    __tablename__ = "note_permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    note_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("notes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permission: Mapped[str] = mapped_column(String(16), nullable=False)
    granted_by: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    note: Mapped[NoteRow] = relationship(back_populates="permissions")

    __table_args__ = (
        UniqueConstraint("note_id", "user_id", name="uq_note_permissions_note_id_user_id"),
        CheckConstraint("permission IN ('read','write')", name="permission_valid"),
        Index("ix_note_permissions_note_id_user_id", "note_id", "user_id"),
        Index("ix_note_permissions_user_id_note_id", "user_id", "note_id"),
    )


__all__ = ["ExternalIdentityRow", "NotePermissionRow", "NoteRow", "UserRow"]
