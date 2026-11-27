"""record enhanced-audit decision evidence on each tool invocation (NEX-055)

NEX-051/052 made a published policy gate the tool gateway and cite the policy
versions it consulted. NEX-053/054 enforced two obligations on that decision
(``redaction`` on the way out, approval on the way in). The ``enhanced-audit``
obligation was still unenforced: a policy could require that an attempt be
"recorded with more evidence than the default", and the gateway recorded exactly
the same row it always did.

This column is that extra evidence. The default trail names the policy
*versions* consulted; it cannot say which *rules* matched, which *obligations*
the decision imposed, or what *attributes* the decision was made against. Those
three are what make the decision reproducible, so they are what the obligation
records — sealed into the invocation's ``intent_hash`` (audit_chain v3 payload)
so the extra proof cannot be added to, trimmed, or rewritten after the fact.

The column is added with ``server_default=''`` and then the default is dropped,
so the migration is safe on an existing deployment: pre-NEX-055 rows read back
as "no enhanced-audit evidence", which is exactly true of them, and they still
verify because an empty evidence field recomputes the earlier intent payload. No
backfill: fabricating evidence for a historical row would assert which rules
decided a call nobody recorded.

Revision ID: a1f4c8d2e635
Revises: b7d2c48e91af
Create Date: 2026-11-27 17:38:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f4c8d2e635"
down_revision: str | None = "b7d2c48e91af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tool_invocations"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        # A server default is required to add a NOT NULL column to a table that
        # already has rows: it backfills them in the same statement.
        batch.add_column(
            sa.Column("decision_evidence", sa.Text(), nullable=False, server_default="")
        )
    # The default is then dropped in its own batch so a writer that forgets the
    # column fails loudly instead of silently recording "no evidence". It must not
    # share the batch above: SQLite implements a batch as create-copy-drop-rename,
    # and a copy into a table that both lacks the column and has no default
    # violates NOT NULL on every pre-existing row.
    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column("decision_evidence", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column("decision_evidence")
