"""agent identity, delegations, tokens, and approval requests

Adds the four tables behind the agent authority chain. Each row is one hop, and
each hop can only narrow the one above it:

    users -> agents -> agent_delegations -> agent_tokens

``agents`` is a registered service principal owned by a user. Its
``capabilities`` column is the *ceiling* — the most it could ever be delegated —
not a grant.

``agent_delegations`` is one user's consent for one agent to act, already
attenuated to a subset of the agent's ceiling. Revoking it must kill derived
tokens immediately, so ``status`` and ``revoked_at`` are read on every token
resolution rather than only at expiry.

``agent_tokens`` holds only the SHA-256 of the secret, matching the session
convention: a database disclosure cannot be replayed as a live credential.
``audience`` scopes a token to one API surface so a token minted for the tool
gateway is not accepted by the Notes API. ``delegation_id`` cascades, so a
deleted delegation cannot leave a token orphaned but usable.

``agent_approval_requests`` records a human decision on one sensitive action.
It is a separate table from tokens on purpose: approval gates *use* of a
capability the agent already holds, it does not grant one.

Revision ID: c3f1a9d47e02
Revises: 8417cdf22d66
Create Date: 2026-11-24 04:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from notes_service.infrastructure.db.base import UTCDateTime

revision: str = "c3f1a9d47e02"
down_revision: str | None = "8417cdf22d66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_identity", sa.String(length=64), nullable=False),
        sa.Column("capabilities", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("max_token_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("revoked_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','suspended','revoked')",
            name=op.f("ck_agents_status_valid"),
        ),
        sa.CheckConstraint(
            "max_token_ttl_seconds > 0 AND max_token_ttl_seconds <= 3600",
            name=op.f("ck_agents_ttl_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_agents_owner_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
        sa.UniqueConstraint("runtime_identity", name=op.f("uq_agents_runtime_identity")),
    )
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_agents_owner_user_id"), ["owner_user_id"], unique=False
        )
        batch_op.create_index("ix_agents_owner_status", ["owner_user_id", "status"], unique=False)

    op.create_table(
        "agent_delegations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("delegator_user_id", sa.Uuid(), nullable=False),
        sa.Column("capabilities", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("revoked_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','revoked')",
            name=op.f("ck_agent_delegations_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_delegations_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["delegator_user_id"],
            ["users.id"],
            name=op.f("fk_agent_delegations_delegator_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_delegations")),
    )
    with op.batch_alter_table("agent_delegations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_agent_delegations_agent_id"), ["agent_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_agent_delegations_delegator_user_id"),
            ["delegator_user_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_agent_delegations_delegator_status",
            ["delegator_user_id", "status"],
            unique=False,
        )

    op.create_table(
        "agent_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("delegation_id", sa.Uuid(), nullable=False),
        sa.Column("audience", sa.String(length=32), nullable=False),
        sa.Column("capabilities", sa.String(length=1024), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.Column("revoked_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "length(token_hash) = 64",
            name=op.f("ck_agent_tokens_token_hash_sha256"),
        ),
        sa.CheckConstraint(
            "audience IN ('notes-api','tool-gateway')",
            name=op.f("ck_agent_tokens_audience_valid"),
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name=op.f("ck_agent_tokens_expiry_after_issue"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_tokens_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["delegation_id"],
            ["agent_delegations.id"],
            name=op.f("fk_agent_tokens_delegation_id_agent_delegations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_tokens")),
    )
    with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_tokens_agent_id"), ["agent_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_agent_tokens_delegation_id"), ["delegation_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_agent_tokens_expires_at"), ["expires_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_agent_tokens_token_hash"), ["token_hash"], unique=True)
        batch_op.create_index(
            "ix_agent_tokens_agent_revoked_at", ["agent_id", "revoked_at"], unique=False
        )

    op.create_table(
        "agent_approval_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("resource_ref", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_at", UTCDateTime(), nullable=False),
        sa.Column("decided_at", UTCDateTime(), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','approved','denied')",
            name=op.f("ck_agent_approval_requests_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_approval_requests_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_agent_approval_requests_decided_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_approval_requests")),
    )
    with op.batch_alter_table("agent_approval_requests", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_agent_approval_requests_agent_id"), ["agent_id"], unique=False
        )
        batch_op.create_index(
            "ix_agent_approval_requests_agent_status",
            ["agent_id", "status"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_approval_requests", schema=None) as batch_op:
        batch_op.drop_index("ix_agent_approval_requests_agent_status")
        batch_op.drop_index(batch_op.f("ix_agent_approval_requests_agent_id"))

    op.drop_table("agent_approval_requests")

    with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
        batch_op.drop_index("ix_agent_tokens_agent_revoked_at")
        batch_op.drop_index(batch_op.f("ix_agent_tokens_token_hash"))
        batch_op.drop_index(batch_op.f("ix_agent_tokens_expires_at"))
        batch_op.drop_index(batch_op.f("ix_agent_tokens_delegation_id"))
        batch_op.drop_index(batch_op.f("ix_agent_tokens_agent_id"))

    op.drop_table("agent_tokens")

    with op.batch_alter_table("agent_delegations", schema=None) as batch_op:
        batch_op.drop_index("ix_agent_delegations_delegator_status")
        batch_op.drop_index(batch_op.f("ix_agent_delegations_delegator_user_id"))
        batch_op.drop_index(batch_op.f("ix_agent_delegations_agent_id"))

    op.drop_table("agent_delegations")

    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_index("ix_agents_owner_status")
        batch_op.drop_index(batch_op.f("ix_agents_owner_user_id"))

    op.drop_table("agents")
