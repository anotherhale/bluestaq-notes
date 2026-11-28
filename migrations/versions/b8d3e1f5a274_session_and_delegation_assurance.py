"""persist authentication assurance on sessions and delegations

Records how strongly the human authenticated (ADR-015) so the ``step-up``
policy obligation can be enforced from evidence instead of guessed. Both columns
are nullable: rows written before this migration have no recorded proof, and an
absent value must read as single-factor rather than as authority.

Revision ID: b8d3e1f5a274
Revises: a1f4c8d2e635
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b8d3e1f5a274"
down_revision: str | None = "a1f4c8d2e635"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("user_sessions", sa.Column("assurance", sa.String(length=32), nullable=True))
    op.add_column("agent_delegations", sa.Column("assurance", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_delegations", "assurance")
    op.drop_column("user_sessions", "assurance")
