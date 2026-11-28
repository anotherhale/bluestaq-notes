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
    text,
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
    #: Assurance the human proved at login (ADR-015). Nullable for rows created
    #: before the column existed; read back as single-factor, never as stepped up.
    assurance: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    # The high-water mark of this agent's audit chain, kept here rather than
    # derived from ``tool_invocations`` so that deleting the newest invocation
    # rows cannot make the chain look complete. See ``audit_chain.ChainAnchor``.
    chain_anchor_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chain_anchor_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)

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
    #: Assurance the delegator proved when granting (ADR-015). Nullable because
    #: rows written before the column existed have no recorded proof, and an
    #: absent value must read as single-factor — never as stepped up.
    assurance: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    # An approval is bounded in time and spendable once. Nullable only for rows
    # written before these columns existed; the aggregate always sets them.
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # A granted approval can be withdrawn before it is spent. The stamp is what
    # distinguishes "allowed, then taken back" from "never allowed" in the audit
    # record, so it is recorded rather than folded into the status alone.
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Why the grant was withdrawn, kept apart from ``reason`` so the approval
    # justification is not lost when the authorisation is taken back.
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Two-person control (NEX-059). ``required_approvals`` is stamped when the
    # request is opened from a policy that imposes the ``second-approver``
    # obligation; the two signatory columns record the distinct humans. The
    # server default of 1 is what every pre-existing row means: one approver was
    # enough, and no backfill can invent a second signature that never happened.
    required_approvals: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    second_approver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    second_decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    second_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    denied_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Optimistic concurrency for the decision itself. A stale save would rewrite
    # ``status``/``consumed_at`` from a pre-decision copy and silently restore
    # authority the human already withdrew or spent.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','denied','revoked')", name="status_valid"),
        # A request may require one or two approvers, never more and never zero:
        # zero would mean "nobody must approve", which is the absence of a control
        # stored as though it were one.
        CheckConstraint("required_approvals IN (1,2)", name="required_approvals_valid"),
        Index("ix_agent_approval_requests_agent_status", "agent_id", "status"),
    )


class ToolInvocationRow(Base):
    """One audited tool invocation through the governed gateway.

    The row is written before the handler runs (``started``) and finalised
    after, so a crash mid-handler is visible as an unfinished invocation rather
    than silently vanishing. ``arguments_digest`` is stored instead of the raw
    arguments: the audit trail must not become a second copy of note content.

    ``idempotency_key`` is unique per agent — not globally — so one agent
    reusing a key cannot block or observe another agent's invocation.

    ``policy_versions`` holds the JSON array of policy versions in force when the
    gateway decided the call (NEX-052), sealed into ``intent_hash``. It is stored
    as JSON text for the same reason ``PolicyVersionRow.rules`` is: the column is
    a durable record of a decision, not a queryable dimension.

    ``decision_evidence`` holds the JSON object of extra proof a policy's
    ``enhanced-audit`` obligation required (NEX-055), also sealed into
    ``intent_hash``: the matched rules, the obligations imposed, and the
    attribute snapshot the decision used. Empty string means "not an
    enhanced-audit attempt", which is the state of every row written before
    NEX-055 and reads back as ``None``.
    """

    __tablename__ = "tool_invocations"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("agent_tokens.id", ondelete="SET NULL"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="started")
    outcome: Mapped[str | None] = mapped_column(String(500), nullable=True)
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("agent_approval_requests.id", ondelete="SET NULL"), nullable=True
    )
    invoked_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    chain_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_versions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decision_evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        CheckConstraint("status IN ('started','succeeded','failed','denied')", name="status_valid"),
        CheckConstraint("length(arguments_digest) = 64", name="digest_sha256"),
        CheckConstraint("length(previous_hash) = 64", name="previous_hash_sha256"),
        CheckConstraint("length(intent_hash) = 64", name="intent_hash_sha256"),
        UniqueConstraint(
            "agent_id", "idempotency_key", name="uq_tool_invocations_agent_idempotency"
        ),
        UniqueConstraint(
            "agent_id", "chain_sequence", name="uq_tool_invocations_agent_chain_sequence"
        ),
        Index("ix_tool_invocations_agent_invoked_at", "agent_id", "invoked_at"),
    )


class AgentRunRow(Base):
    """One durable agent run: an ordered plan of governed tool steps.

    Unlike ``tool_invocations``, which stores only a digest of the arguments,
    a run row persists each step's arguments. Durability needs the inputs — a
    resumed run cannot re-derive what a step was asked to do — and the row is
    owner-scoped working state rather than a broadly readable audit trail.
    """

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delegation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("agent_delegations.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','interrupted','completed','failed','aborted')",
            name="status_valid",
        ),
        Index("ix_agent_runs_agent_status", "agent_id", "status"),
    )


class AgentRunStepRow(Base):
    """One step of a durable run, with the inputs a resume needs to replay it."""

    __tablename__ = "agent_run_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[str] = mapped_column(Text, nullable=False)
    resource_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    outcome: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','started','succeeded','failed','interrupted')",
            name="status_valid",
        ),
        CheckConstraint("length(arguments_digest) = 64", name="digest_sha256"),
        UniqueConstraint("run_id", "sequence", name="uq_agent_run_steps_run_sequence"),
    )


class OperatorGrantRow(Base):
    """One operator's permission to read one platform-wide operation (NEX-046).

    Grants used to live in a process, so a restart forgot every bounded grant and
    each worker held its own copy. This row is the durable form.

    ``id`` is a surrogate key rather than a composite one for two reasons: the
    port promises ``active_grants`` in *insertion order*, and an autoincrementing
    integer is the only portable way to recover it; and ``granted_at`` is a
    business field whose precision must not be load-bearing for identity. The
    natural key the domain cares about is
    ``(user_id, scope, granted_at)``, enforced by a unique constraint, so a seed
    that runs twice cannot accumulate two grants for the same fact.

    ``granted_by`` is nullable and that is meaningful: ``NULL`` marks a grant the
    deployment configuration issued, which is what lets the seed path reconcile
    its own grants without touching grants a human issued.

    ``rationale`` is the *why* (NEX-076). A human-issued grant carries the
    operator's stated reason; a configuration grant has none, because its reason is
    the deployment itself. That pairing is enforced where a *person* supplies it,
    not by this column's nullability: ``granted_by`` is a ``SET NULL`` foreign key,
    so deleting the issuer's account leaves a NULL issuer beside a surviving
    ``rationale`` (NEX-080). The alternative — refusing that shape — made every
    read of the store, including the gate lookup, raise once an issuer was
    deleted. Storing the reason still closes the gap where intent lived only in
    the access log, which recorded the issuer and instant but never the reason.
    """

    __tablename__ = "operator_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Revocation (NEX-079): recorded as a fact rather than applied by deleting the
    # row, so an audit can see authority was taken away, by whom, and when. The
    # revoke path writes both columns together; the CHECK below only forbids the
    # incoherent half (an actor with no instant), because ``revoked_by`` is SET NULL
    # on user deletion and a revocation deliberately outlives its revoker. That is
    # the point: losing the record because the revoker's account was later removed
    # would erase the revocation itself.
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("user_id", "scope", "granted_at", name="uq_operator_grants_identity"),
        CheckConstraint("length(scope) > 0", name="scope_not_empty"),
        CheckConstraint("rationale IS NULL OR length(rationale) > 0", name="rationale_not_empty"),
        # A revocation instant may outlive its actor (NEX-080): ``revoked_by`` is a
        # ``SET NULL`` foreign key, so deleting the revoker's account legally leaves
        # ``revoked_at`` set with a NULL ``revoked_by`` — the record of the
        # revocation survives, which is its whole purpose. The constraint therefore
        # only forbids the genuinely incoherent half: a named revoker with no
        # instant, which reads as a revocation that never happened.
        CheckConstraint(
            "revoked_by IS NULL OR revoked_at IS NOT NULL",
            name="revocation_actor_requires_instant",
        ),
        Index("ix_operator_grants_user_scope", "user_id", "scope"),
    )


class PolicyVersionRow(Base):
    """One governance policy version, durably stored (NEX-048).

    ``InMemoryPolicyVersionRepository`` proved the port but forgot every version on
    restart, including published ones — so a restart emptied the policy set, and an
    empty policy set denies everything. That is fail-closed, but it is an
    operational gap rather than a design choice. This row is the durable form.

    The two lifecycle invariants live in the schema, not only in the use case, so a
    future caller that bypasses ``PublishDraft`` cannot corrupt history:

    * ``UNIQUE (policy_id, version_number)`` — two authors cannot both claim v4.
    * a **partial** unique index on ``(policy_id) WHERE status = 'PUBLISHED'`` — at
      most one version of a policy is ever in force. The use case already retires
      the predecessor before publishing; this makes the rule true regardless.

    ``rules`` holds the policy AST serialised to JSON. It is re-validated through
    the domain on read, because a stored policy is untrusted input on the way back
    in: a row written by older code must not be able to reintroduce a node type the
    current validator refuses.

    ``id`` is a surrogate key so ``history`` can recover insertion order without
    relying on ``created_at`` precision, and it is what makes an ``UPDATE ... WHERE
    id = ?`` unambiguous when two rows share a version number across policy ids.

    ``drafted_by`` records the author, and a third schema rule enforces separation of
    duties (NEX-050) against a row written by something other than the use cases: a
    published or retired row must name an author *different* from its publisher. The
    column is nullable only so a pre-NEX-050 row remains readable, and the constraint
    therefore tests for inequality alone - a NULL author must survive the table
    rebuild an upgrade performs, because that rebuild sees the legacy row before any
    application code does (NEX-101). A table-wide CHECK cannot distinguish a
    grandfathered row from a malformed new one, so *presence* of the author is not
    this constraint's job: the domain refuses to publish a version with no recorded
    author, and no application path can reach the store without one.
    """

    __tablename__ = "policy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    rationale: Mapped[str] = mapped_column(String(400), nullable=False)
    rules: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    drafted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("policy_id", "version_number", name="uq_policy_versions_policy_version"),
        CheckConstraint(
            "status IN ('draft','approved','published','retired')",
            name="policy_versions_status_valid",
        ),
        CheckConstraint("version_number >= 1", name="policy_versions_version_positive"),
        CheckConstraint("length(policy_id) > 0", name="policy_versions_policy_id_not_empty"),
        # Each lifecycle state carries exactly the record it is entitled to: a draft
        # has none, an approved version has an approval and no publication, and a
        # version in force has a publication and - unless it predates NEX-065 - an
        # approval too. The domain enforces this on construction; the schema
        # enforces it against a row written by something other than the domain, so
        # an approval cannot be invented after the fact. The approval is optional
        # for a version in force only because c4e7a1b93d05 must rebuild a table that
        # may already hold such a row; a half-written approval is still refused.
        CheckConstraint(
            "(status = 'draft'"
            " AND published_at IS NULL AND published_by IS NULL"
            " AND approved_at IS NULL AND approved_by IS NULL)"
            " OR (status = 'approved'"
            " AND approved_at IS NOT NULL AND approved_by IS NOT NULL"
            " AND published_at IS NULL AND published_by IS NULL)"
            " OR (status IN ('published','retired')"
            " AND published_at IS NOT NULL AND published_by IS NOT NULL"
            " AND ((approved_at IS NULL AND approved_by IS NULL)"
            " OR (approved_at IS NOT NULL AND approved_by IS NOT NULL)))",
            name="policy_versions_publication_data_consistent",
        ),
        # Separation of duties (NEX-050, extended by NEX-065 and NEX-078): the
        # author, approver and publisher of a version in force are pairwise
        # distinct. An author approving their own rules is not a review, an author
        # publishing their own rules has forced them, and an approver who also
        # publishes has performed both halves of the two-person control alone.
        #
        # The predicate is *comparisons only* - no ``drafted_by IS NOT NULL`` and no
        # ``approved_by IS NOT NULL``. A legacy in-force row carries NULL in both,
        # and ``NULL IS NOT NULL`` is FALSE, so such a guard would be violated by
        # history; ``NULL <> 'x'`` is NULL, which a CHECK passes. Since a CHECK is
        # table-wide it cannot tell a grandfathered row from a malformed new one,
        # so author/approver *presence* is enforced where it can be: the domain
        # (``separation_of_duties``, ``approval_of_duties``) and ``PublishDraft``.
        # Do not "tighten" this by adding an IS NOT NULL guard - that is precisely
        # what made ``upgrade head`` impossible on a database with a published
        # legacy policy (NEX-101).
        CheckConstraint(
            "status = 'draft'"
            " OR (status = 'approved' AND drafted_by <> approved_by)"
            " OR (status IN ('published','retired')"
            " AND drafted_by <> published_by AND drafted_by <> approved_by"
            " AND approved_by <> published_by)",
            name="policy_versions_separation_of_duties",
        ),
        Index("ix_policy_versions_policy_status", "policy_id", "status"),
        Index(
            "uq_policy_versions_one_published",
            "policy_id",
            unique=True,
            sqlite_where=text("status = 'published'"),
            postgresql_where=text("status = 'published'"),
        ),
    )


class OperatorAccessRow(Base):
    """One granted read of a platform-wide operational view, durably stored (NEX-072).

    ``InMemoryOperatorAccessLog`` proved the port but kept the record in the
    process: it was per-worker and cleared on restart, so the answer to "who read
    this" was lost the moment the deployment restarted — and in a multi-worker
    deployment the log an operator could read described only the worker that
    answered the read. That is the same defect R40 recorded for grants, on the
    record that exists *because* a privileged read must be auditable.

    The table is append-only by construction: there is no update path in the
    adapter and ``record`` only ever inserts. ``id`` is a surrogate autoincrement
    key because the port promises ``recent`` newest-first, and ordering by a
    monotonic integer is the only portable way to recover insertion order — two
    reads can share a timestamp, and sorting by ``at`` would make the order
    implementation-defined.

    ``user_id`` is **not** a foreign key to ``users`` and does not cascade. An
    access record must outlive the deletion of the account that made it: cascading
    would let deleting a user erase the evidence of what that user read, which is
    exactly what an audit trail must not permit. The id is stored as a value.
    """

    __tablename__ = "operator_access_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)

    __table_args__ = (
        CheckConstraint("length(operation) > 0", name="operation_not_empty"),
        Index("ix_operator_access_log_at_id", "at", "id"),
    )


__all__ = [
    "AgentRow",
    "AgentRunRow",
    "AgentRunStepRow",
    "AgentTokenRow",
    "ApprovalRequestRow",
    "DelegationRow",
    "ExternalIdentityRow",
    "FolderRow",
    "LoginTransactionRow",
    "NotePermissionRow",
    "NoteRow",
    "NoteStarRow",
    "OperatorAccessRow",
    "OperatorGrantRow",
    "OrganizationMembershipRow",
    "OrganizationRow",
    "OrganizationUnitRow",
    "PolicyVersionRow",
    "TeamMembershipRow",
    "TeamRow",
    "ToolInvocationRow",
    "UserRow",
    "UserSessionRow",
]
