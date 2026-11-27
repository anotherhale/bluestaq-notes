"""tool invocations audit trail for the governed tool gateway

Adds ``tool_invocations``: one row per attempt to invoke a tool through the
gateway, whether it succeeded, failed, or was denied. The row is written before
the handler runs (``started``) and finalised afterwards, so a crash mid-handler
leaves an unfinished invocation visible rather than vanishing.

``arguments_digest`` stores a SHA-256 of the canonicalised (tool, arguments)
rather than the arguments themselves, so the audit trail cannot become a second
copy of note content. ``idempotency_key`` is unique **per agent**, matching the
gateway's rule that one agent reusing a key cannot block or observe another
agent's invocation.

Revision ID: 5d0b71f8ac34
Revises: c3f1a9d47e02
Create Date: 2026-11-24 08:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from notes_service.infrastructure.db.base import UTCDateTime

revision: str = "5d0b71f8ac34"
down_revision: str | None = "c3f1a9d47e02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("token_id", sa.Uuid(), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("arguments_digest", sa.String(length=64), nullable=False),
        sa.Column("resource_ref", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=500), nullable=True),
        sa.Column("approval_request_id", sa.Uuid(), nullable=True),
        sa.Column("invoked_at", UTCDateTime(), nullable=False),
        sa.Column("completed_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('started','succeeded','failed','denied')",
            name=op.f("ck_tool_invocations_status_valid"),
        ),
        sa.CheckConstraint(
            "length(arguments_digest) = 64",
            name=op.f("ck_tool_invocations_digest_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_tool_invocations_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["token_id"],
            ["agent_tokens.id"],
            name=op.f("fk_tool_invocations_token_id_agent_tokens"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["agent_approval_requests.id"],
            name=op.f("fk_tool_invocations_approval_request_id_agent_approval_requests"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_invocations")),
        sa.UniqueConstraint(
            "agent_id",
            "idempotency_key",
            name=op.f("uq_tool_invocations_agent_idempotency"),
        ),
    )
    with op.batch_alter_table("tool_invocations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_tool_invocations_agent_id"), ["agent_id"], unique=False
        )
        batch_op.create_index(
            "ix_tool_invocations_agent_invoked_at",
            ["agent_id", "invoked_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("tool_invocations", schema=None) as batch_op:
        batch_op.drop_index("ix_tool_invocations_agent_invoked_at")
        batch_op.drop_index(batch_op.f("ix_tool_invocations_agent_id"))
    op.drop_table("tool_invocations")
