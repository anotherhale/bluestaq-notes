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
    # Null means the note lives in its owner's root ("My Drive"). SET NULL on
    # folder deletion so a deleted folder can never orphan a note.
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("folders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
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
        Index("ix_notes_owner_id_folder_id", "owner_id", "folder_id"),
    )


class FolderRow(Base):
    __tablename__ = "folders"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("length(name) >= 1", name="name_not_empty"),
        # Folder names are unique per owner: two folders with the same name would
        # be indistinguishable in the navigation tree.
        UniqueConstraint("owner_id", "name", name="uq_folders_owner_id_name"),
    )


class NoteStarRow(Base):
    """A per-user star on a note. Composite primary key; no surrogate id needed."""

    __tablename__ = "note_stars"

    note_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (Index("ix_note_stars_user_id_note_id", "user_id", "note_id"),)


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


# -- organization hierarchy ------------------------------------------------
#
# The organization is the tenant boundary. Every child table carries an
# explicit ``organization_id`` even where it could be derived through a parent,
# so that a tenant-scoped query is a single indexed predicate rather than a
# join. That redundancy is deliberate and is why each composite unique
# constraint includes the organization id.


class OrganizationRow(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    slug: Mapped[str] = mapped_column(String(63), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("status IN ('active','suspended','archived')", name="status_valid"),
        CheckConstraint("length(name) >= 1", name="name_not_empty"),
        CheckConstraint("length(slug) >= 1", name="slug_not_empty"),
    )


class OrganizationUnitRow(Base):
    __tablename__ = "organization_units"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Self-referencing parent. ``RESTRICT`` is intentional: a unit with children
    # must be re-parented or its children archived first, so an accidental
    # delete can never silently collapse a subtree.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("organization_units.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    unit_type: Mapped[str] = mapped_column(String(16), nullable=False, default="department")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "slug", name="uq_organization_units_organization_id_slug"
        ),
        CheckConstraint(
            "unit_type IN ('division','department','branch','program')", name="unit_type_valid"
        ),
        CheckConstraint("status IN ('active','archived')", name="status_valid"),
        CheckConstraint("length(name) >= 1", name="name_not_empty"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="parent_not_self"),
        Index("ix_organization_units_org_parent", "organization_id", "parent_id"),
    )


class TeamRow(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("organization_units.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    parent_team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("teams.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("unit_id", "slug", name="uq_teams_unit_id_slug"),
        CheckConstraint("status IN ('active','archived')", name="status_valid"),
        CheckConstraint("length(name) >= 1", name="name_not_empty"),
        CheckConstraint("parent_team_id IS NULL OR parent_team_id <> id", name="parent_not_self"),
        Index("ix_teams_org_unit", "organization_id", "unit_id"),
    )


class OrganizationMembershipRow(Base):
    """User membership in an organization, with the scoped reporting line.

    ``manager_user_id`` is scoped here rather than in a global table because a
    manager relationship is only meaningful inside one organization.
    """

    __tablename__ = "organization_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="org_member")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_organization_memberships_organization_id_user_id"
        ),
        CheckConstraint(
            "role IN ('org_owner','org_admin','org_security_admin','org_auditor','org_member')",
            name="role_valid",
        ),
        CheckConstraint("status IN ('active','suspended','deprovisioned')", name="status_valid"),
        CheckConstraint(
            "manager_user_id IS NULL OR manager_user_id <> user_id", name="manager_not_self"
        ),
        Index(
            "ix_organization_memberships_org_status",
            "organization_id",
            "status",
        ),
    )


class TeamMembershipRow(Base):
    __tablename__ = "team_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="team_member")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_id_user_id"),
        CheckConstraint("role IN ('team_lead','team_member')", name="role_valid"),
        CheckConstraint("status IN ('active','suspended','deprovisioned')", name="status_valid"),
        Index("ix_team_memberships_user_id_status", "user_id", "status"),
    )


class LoginTransactionRow(Base):
    """One pending OIDC login attempt, keyed by its single-use ``state``.

    Rows are short-lived (minutes) and are deleted once consumed, so the table
    stays small. ``state`` is unique because it is the lookup key the callback
    arrives with; a duplicate would be ambiguous and is rejected by the schema.
    """

    __tablename__ = "login_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    state: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    nonce: Mapped[str] = mapped_column(String(255), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("length(state) >= 1", name="state_not_empty"),
        CheckConstraint("length(nonce) >= 1", name="nonce_not_empty"),
        CheckConstraint("length(code_verifier) >= 1", name="code_verifier_not_empty"),
        Index("ix_login_transactions_expires_at_consumed_at", "expires_at", "consumed_at"),
    )


class UserSessionRow(Base):
    """An established authenticated session.

    Only the SHA-256 hash of the cookie secret is stored, so a database
    disclosure cannot be replayed as a live session. ``token_hash`` is unique so
    one cookie maps to exactly one session row.
    """

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    idle_expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    absolute_expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("length(token_hash) = 64", name="token_hash_sha256"),
        CheckConstraint("absolute_expires_at >= created_at", name="absolute_after_created"),
        Index("ix_user_sessions_user_id_revoked_at", "user_id", "revoked_at"),
    )


class AgentRow(Base):
    """A registered service principal and its capability ceiling.

    ``capabilities`` is stored as a comma-separated string rather than a JSON
    column so the same DDL works unchanged on SQLite and PostgreSQL; the set is
    small, fixed vocabulary and is validated by the domain on the way out.
    """

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    runtime_identity: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    capabilities: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    max_token_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('active','suspended','revoked')", name="status_valid"),
        CheckConstraint(
            "max_token_ttl_seconds > 0 AND max_token_ttl_seconds <= 3600",
            name="ttl_bounded",
        ),
        Index("ix_agents_owner_status", "owner_user_id", "status"),
    )


class DelegationRow(Base):
    """One user's consent for one agent to act, narrowed to a capability set."""

    __tablename__ = "agent_delegations"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delegator_user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    capabilities: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('active','revoked')", name="status_valid"),
        Index("ix_agent_delegations_delegator_status", "delegator_user_id", "status"),
    )


class AgentTokenRow(Base):
    """An issued agent access token.

    Only the SHA-256 hash of the secret is stored, matching the session-cookie
    convention: a database disclosure cannot be replayed as a live credential.
    """

    __tablename__ = "agent_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delegation_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("agent_delegations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    audience: Mapped[str] = mapped_column(String(32), nullable=False)
    capabilities: Mapped[str] = mapped_column(String(1024), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("length(token_hash) = 64", name="token_hash_sha256"),
        CheckConstraint("audience IN ('notes-api','tool-gateway')", name="audience_valid"),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        Index("ix_agent_tokens_agent_revoked_at", "agent_id", "revoked_at"),
    )


class ApprovalRequestRow(Base):
    """A human decision on one sensitive action an agent wants to take."""

    __tablename__ = "agent_approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','denied')", name="status_valid"),
        Index("ix_agent_approval_requests_agent_status", "agent_id", "status"),
    )


__all__ = [
    "AgentRow",
    "AgentTokenRow",
    "ApprovalRequestRow",
    "DelegationRow",
    "ExternalIdentityRow",
    "FolderRow",
    "LoginTransactionRow",
    "NotePermissionRow",
    "NoteRow",
    "NoteStarRow",
    "OrganizationMembershipRow",
    "OrganizationRow",
    "OrganizationUnitRow",
    "TeamMembershipRow",
    "TeamRow",
    "UserRow",
    "UserSessionRow",
]
