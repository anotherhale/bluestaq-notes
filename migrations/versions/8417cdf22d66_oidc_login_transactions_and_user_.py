"""oidc login transactions and user sessions

Adds the two tables the OIDC authorization-code flow needs.

``login_transactions`` holds one short-lived row per in-flight login, keyed by
the single-use ``state`` the callback returns. It stores the ``nonce`` and PKCE
``code_verifier`` so the callback can validate the id_token and redeem the code
without trusting anything carried in the browser. Rows are deleted once
consumed, and ``expires_at`` bounds how long an unfinished login can linger.

``user_sessions`` is the established session, keyed by the SHA-256 of the
cookie secret. Storing only the hash means a database disclosure cannot be
replayed as a live session. Two clocks are tracked: ``idle_expires_at`` (slid
forward on each use) and ``absolute_expires_at`` (never extended), so a session
always has a hard ceiling regardless of activity. ``revoked_at`` supports
immediate revocation.

Revision ID: 8417cdf22d66
Revises: 189c359206d3
Create Date: 2026-11-24 01:26:33.525963
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from notes_service.infrastructure.db.base import UTCDateTime

revision: str = "8417cdf22d66"
down_revision: str | None = "189c359206d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("nonce", sa.String(length=255), nullable=False),
        sa.Column("code_verifier", sa.String(length=255), nullable=False),
        sa.Column("redirect_uri", sa.String(length=2048), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.Column("consumed_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "length(code_verifier) >= 1",
            name=op.f("ck_login_transactions_code_verifier_not_empty"),
        ),
        sa.CheckConstraint(
            "length(nonce) >= 1", name=op.f("ck_login_transactions_nonce_not_empty")
        ),
        sa.CheckConstraint(
            "length(state) >= 1", name=op.f("ck_login_transactions_state_not_empty")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_transactions")),
    )
    with op.batch_alter_table("login_transactions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_login_transactions_expires_at"), ["expires_at"], unique=False
        )
        batch_op.create_index(
            "ix_login_transactions_expires_at_consumed_at",
            ["expires_at", "consumed_at"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_login_transactions_state"), ["state"], unique=True)

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("last_seen_at", UTCDateTime(), nullable=False),
        sa.Column("idle_expires_at", UTCDateTime(), nullable=False),
        sa.Column("absolute_expires_at", UTCDateTime(), nullable=False),
        sa.Column("revoked_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "absolute_expires_at >= created_at",
            name=op.f("ck_user_sessions_absolute_after_created"),
        ),
        sa.CheckConstraint(
            "length(token_hash) = 64", name=op.f("ck_user_sessions_token_hash_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
    )
    with op.batch_alter_table("user_sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_sessions_idle_expires_at"), ["idle_expires_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_user_sessions_token_hash"), ["token_hash"], unique=True
        )
        batch_op.create_index(batch_op.f("ix_user_sessions_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            "ix_user_sessions_user_id_revoked_at", ["user_id", "revoked_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("user_sessions", schema=None) as batch_op:
        batch_op.drop_index("ix_user_sessions_user_id_revoked_at")
        batch_op.drop_index(batch_op.f("ix_user_sessions_user_id"))
        batch_op.drop_index(batch_op.f("ix_user_sessions_token_hash"))
        batch_op.drop_index(batch_op.f("ix_user_sessions_idle_expires_at"))

    op.drop_table("user_sessions")
    with op.batch_alter_table("login_transactions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_login_transactions_state"))
        batch_op.drop_index("ix_login_transactions_expires_at_consumed_at")
        batch_op.drop_index(batch_op.f("ix_login_transactions_expires_at"))

    op.drop_table("login_transactions")
