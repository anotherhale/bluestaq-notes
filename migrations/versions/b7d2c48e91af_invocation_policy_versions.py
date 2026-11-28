"""record the deciding policy versions on each tool invocation (NEX-052)

NEX-051 put published policy in front of the tool gateway, but the decision it
made was not durable: ``PolicyDecision.policy_versions`` was computed, used to
refuse or allow, and then thrown away. The audit row kept only a free-text
outcome, so after a policy was retired nothing could answer "which version of
which policy was in force when this call was allowed?". The security
architecture names "matched policy versions" as a field of an auditable
decision; this column is that field.

The citation is sealed into the invocation's ``intent_hash`` (audit_chain v2
payload), so it cannot be edited after the fact without breaking the chain — the
same guarantee the rest of the attempt already has.

The column is added with ``server_default=''`` and then the default is dropped,
so the same migration is safe on an existing deployment with rows in it:
pre-NEX-052 rows read back as "no policy cited", which is exactly true of them,
and they still verify because an empty citation recomputes the v1 intent
payload. No backfill: inventing a citation for a historical row would assert
which policy governed a decision nobody recorded.

Revision ID: b7d2c48e91af
Revises: 9c4d1e8a7b23
Create Date: 2026-11-27 16:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2c48e91af"
down_revision: str | None = "9c4d1e8a7b23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tool_invocations"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        # A server default is required to add a NOT NULL column to a table that
        # already has rows: it backfills them in the same statement.
        batch.add_column(sa.Column("policy_versions", sa.Text(), nullable=False, server_default=""))
    # The default is then dropped, in its own batch, so a writer that forgets the
    # column fails loudly instead of silently recording "no policy cited". It must
    # not share the batch above: SQLite implements a batch as create-copy-drop-
    # rename, and a copy into a table that both lacks the column and has no
    # default violates NOT NULL on every pre-existing row.
    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column("policy_versions", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column("policy_versions")
