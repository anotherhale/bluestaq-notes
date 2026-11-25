"""bound approval validity and make approvals single-use

An approval authorised a sensitive agent action *forever*: the gateway matched
an ``approved`` row on (agent, capability, resource) and never spent or expired
it, so one human decision to delete a note authorised every later delete of
that note. The security architecture (§11) caps approval validity (High tier:
72 hours) and requires that an expired approval block execution.

Adds two nullable timestamp columns and backfills ``expires_at`` for existing
rows from ``requested_at + 72h``. The backfill is deliberately fail-closed: any
approval old enough to predate this migration is already past its window, so it
becomes unusable rather than silently retaining indefinite authority.

Columns stay nullable for backward compatibility with any row written by an
older application version mid-deploy; the aggregate always populates them.

Revision ID: d4c8e2a71f05
Revises: b2e5c81f4a07
Create Date: 2026-11-25 06:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from notes_service.domain.agent.entities import DEFAULT_APPROVAL_TTL
from notes_service.infrastructure.db.base import UTCDateTime

revision: str = "d4c8e2a71f05"
down_revision: str | None = "b2e5c81f4a07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "agent_approval_requests"


def _as_datetime(value: object) -> datetime:
    """Coerce a raw driver value to a datetime.

    A plain ``sa.text()`` SELECT bypasses the column's type decorator, so the
    driver's native representation arrives untouched: PostgreSQL returns a
    datetime, SQLite returns the stored string. Concatenating a timedelta to the
    string raises TypeError and aborts the migration mid-upgrade, so the
    backfill must normalise first.
    """
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("expires_at", UTCDateTime(), nullable=True))
    op.add_column(_TABLE, sa.Column("consumed_at", UTCDateTime(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(f"SELECT id, requested_at FROM {_TABLE} WHERE expires_at IS NULL")
    ).fetchall()
    for row_id, requested_at in rows:
        bind.execute(
            sa.text(f"UPDATE {_TABLE} SET expires_at = :expires_at WHERE id = :id"),
            {"expires_at": _as_datetime(requested_at) + DEFAULT_APPROVAL_TTL, "id": row_id},
        )


def downgrade() -> None:
    op.drop_column(_TABLE, "consumed_at")
    op.drop_column(_TABLE, "expires_at")
