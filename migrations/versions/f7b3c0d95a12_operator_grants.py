"""store operator grants so a grant survives a restart

``OperatorGrant`` (NEX-045) made an operator permission a record with a lifetime
and a holder, but the only adapter was in-memory: a restart forgot every bounded
grant and each worker in a multi-worker deployment held its own copy, so a grant
issued on one worker was invisible to the others. R40 records that gap. This
migration is the durable half.

No backfill. Grants are seeded from configuration at startup and issued through
the store thereafter, and there is no earlier table whose rows this could
reconstruct. A deployment upgrading with grants in memory re-seeds them on the
next boot; that is the same set the environment variable already describes.

Revision ID: f7b3c0d95a12
Revises: c7a91e3d5b62
Create Date: 2026-11-27 12:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from notes_service.infrastructure.db.base import UTCDateTime, UUIDType

revision: str = "f7b3c0d95a12"
down_revision: str | None = "c7a91e3d5b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "operator_grants"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", UUIDType, nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("granted_at", UTCDateTime(), nullable=False),
        sa.Column("granted_by", UUIDType, nullable=True),
        sa.Column("not_before", UTCDateTime(), nullable=True),
        sa.Column("expires_at", UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operator_grants")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_operator_grants_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_operator_grants_granted_by_users"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "user_id", "scope", "granted_at", name=op.f("uq_operator_grants_identity")
        ),
        sa.CheckConstraint("length(scope) > 0", name=op.f("ck_operator_grants_scope_not_empty")),
    )
    op.create_index(op.f("ix_operator_grants_user_id"), _TABLE, ["user_id"], unique=False)
    op.create_index(
        op.f("ix_operator_grants_user_scope"), _TABLE, ["user_id", "scope"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_operator_grants_user_scope"), table_name=_TABLE)
    op.drop_index(op.f("ix_operator_grants_user_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
