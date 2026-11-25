"""make granted approvals revocable

An approval is live authority from the moment a human grants it until it is
spent or expires. The security architecture (§11) requires that *expired,
revoked and invalidated* be distinct statuses that all block execution — but
the status vocabulary had no ``revoked`` member and no revocation stamp, so a
human who approved by mistake could only wait out the 72-hour window or let the
agent act. Neither is an acceptable answer to "take that back".

Adds the ``revoked`` status value plus ``revoked_at`` / ``revoked_by_user_id``,
so the audit trail records *who* withdrew an authorisation and *when*, which is
what separates "granted then taken back" from "never granted" in a review.

No backfill is needed: existing rows are pending, approved, denied or spent, and
none of them carries a revocation. The two columns stay nullable so a row
written by an older application version mid-deploy still inserts.

Revision ID: e5a3b9c04f21
Revises: d4c8e2a71f05
Create Date: 2026-11-25 07:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from notes_service.infrastructure.db.base import UTCDateTime

revision: str = "e5a3b9c04f21"
down_revision: str | None = "d4c8e2a71f05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "agent_approval_requests"
_OLD_STATUSES = "status IN ('pending','approved','denied')"
_NEW_STATUSES = "status IN ('pending','approved','denied','revoked')"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.add_column(sa.Column("revoked_at", UTCDateTime(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "revoked_by_user_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "users.id",
                    name=op.f("fk_agent_approval_requests_revoked_by_user_id_users"),
                    ondelete="SET NULL",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("revoke_reason", sa.Text(), nullable=True))
        batch_op.drop_constraint(op.f("ck_agent_approval_requests_status_valid"), type_="check")
        batch_op.create_check_constraint("status_valid", _NEW_STATUSES)


def downgrade() -> None:
    # A revoked row cannot survive the narrower vocabulary, so fold it back into
    # the status that means "not usable". ``denied`` is the honest choice: the
    # authorisation did not result in the action being allowed.
    op.execute(sa.text(f"UPDATE {_TABLE} SET status = 'denied' WHERE status = 'revoked'"))
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_agent_approval_requests_status_valid"), type_="check")
        batch_op.create_check_constraint("status_valid", _OLD_STATUSES)
        batch_op.drop_column("revoke_reason")
        batch_op.drop_column("revoked_by_user_id")
        batch_op.drop_column("revoked_at")
