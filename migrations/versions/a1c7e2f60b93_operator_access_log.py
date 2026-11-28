"""store the operator access log so a privileged read stays auditable

``OperatorAccessLog`` (R36) made a granted read of a platform-wide operational
view a record rather than a log line in a pipeline, but the only adapter was a
process-local ring. R42 records the gap: the record was per-worker and cleared on
restart, so in a multi-worker deployment the report described only the worker that
answered it, and a restart erased the evidence. This migration is the durable
half.

No backfill. The in-memory ring was never durable, so there is no earlier table
whose rows this could reconstruct; a deployment upgrading starts with an empty
trail, which is the honest starting state.

Revision ID: a1c7e2f60b93
Revises: c4e7a1b93d05
Create Date: 2026-11-27 17:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from notes_service.infrastructure.db.base import UTCDateTime, UUIDType

revision: str = "a1c7e2f60b93"
down_revision: str | None = "c4e7a1b93d05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "operator_access_log"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", UUIDType, nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operator_access_log")),
        sa.CheckConstraint(
            "length(operation) > 0", name=op.f("ck_operator_access_log_operation_not_empty")
        ),
    )
    op.create_index(op.f("ix_operator_access_log_user_id"), _TABLE, ["user_id"], unique=False)
    op.create_index(op.f("ix_operator_access_log_at"), _TABLE, ["at"], unique=False)
    op.create_index(op.f("ix_operator_access_log_at_id"), _TABLE, ["at", "id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_operator_access_log_at_id"), table_name=_TABLE)
    op.drop_index(op.f("ix_operator_access_log_at"), table_name=_TABLE)
    op.drop_index(op.f("ix_operator_access_log_user_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
