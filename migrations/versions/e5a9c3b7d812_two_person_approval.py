"""require two distinct approvers for a second-approver obligation

Adds the storage for the ``second-approver`` policy obligation (NEX-059). Until
this migration the obligation was declared, parseable and consultable, but
nothing enforced it: an approval was satisfied by one human no matter what the
published policy said, so a two-person control read as a control in the audit
trail while behaving as a one-person grant.

``required_approvals`` carries the threshold on the request itself rather than
being re-derived at spend time. The server default of 1 is exactly what every
existing row means — one signature was sufficient when it was written — so no
backfill can manufacture a second approval that never took place.

Revision ID: e5a9c3b7d812
Revises: b8d3e1f5a274
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e5a9c3b7d812"
down_revision: str | None = "b8d3e1f5a274"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE = "agent_approval_requests"


def upgrade() -> None:
    # Batch mode so SQLite (used by the test suite and local dev) can apply the
    # added constraints via its copy-and-move strategy; the plain ALTER path
    # raises NotImplementedError for constraints on that dialect.
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("required_approvals", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("second_approver_user_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("second_decided_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("second_reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("denied_by_user_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_approval_requests_second_approver",
            "users",
            ["second_approver_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_agent_approval_requests_denied_by",
            "users",
            ["denied_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "required_approvals_valid",
            "required_approvals IN (1,2)",
        )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_constraint("required_approvals_valid", type_="check")
        batch_op.drop_constraint("fk_agent_approval_requests_denied_by", type_="foreignkey")
        batch_op.drop_constraint("fk_agent_approval_requests_second_approver", type_="foreignkey")
        batch_op.drop_column("denied_by_user_id")
        batch_op.drop_column("second_reason")
        batch_op.drop_column("second_decided_at")
        batch_op.drop_column("second_approver_user_id")
        batch_op.drop_column("required_approvals")
