"""give approval requests an optimistic-concurrency version

An approval request is live authority, so every decision on it — approve, deny,
revoke, consume — changes whether it can authorise an action. Until now the row
carried no version and ``SqlAlchemyApprovalRequestRepository.save`` rewrote the
whole row from whatever the caller had loaded. A stale writer could therefore
write ``approved`` back over a ``revoked`` row (restoring withdrawn authority)
or clear ``consumed_at`` on a spent approval (making it spendable twice), with
no error and no trace. This is the same lost-update class that NEX-019 fixed for
``agent_runs``.

Adds ``version`` with a server-side default of 1 so existing rows are valid
without a backfill and an older application version mid-deploy still inserts.
The compare-and-set lives in the adapter; the column is what makes it possible.

Revision ID: f19a2c6d8b04
Revises: e5a3b9c04f21
Create Date: 2026-11-25 10:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f19a2c6d8b04"
down_revision: str | None = "e5a3b9c04f21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_approval_requests",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    with op.batch_alter_table("agent_approval_requests") as batch:
        batch.drop_column("version")
