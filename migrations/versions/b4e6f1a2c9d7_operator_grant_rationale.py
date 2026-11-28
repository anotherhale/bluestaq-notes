"""record why a human issued an operator grant

``OperatorGrant`` answers who may do what until when, and since NEX-074 an
operator can hand a bounded grant to another user. What it could not answer is
*why*, because the grant row had no column for a reason: ``IssueOperatorGrant``
accepted no rationale, and intent rested on ``granted_by`` plus the gate's access
log — enough to name the issuer and the instant, never the reason. An audit of a
privileged hand-out can reconstruct *that* it happened but not *why* it was
justified, which is the question an auditor actually asks.

This adds the column. It is nullable, and its nullability is meaningful and
mirrors ``granted_by`` exactly:

* a **human-issued** grant (``granted_by`` set) always carries a rationale — the
  domain refuses to construct one without it (NEX-076);
* a **configuration** grant (``granted_by IS NULL``) never carries one, because
  its reason is the deployment itself, which is reviewed with the deployment.

No backfill. Existing rows predate the requirement and there is nothing to
reconstruct a reason from; inventing one would fabricate an audit fact, which is
worse than an honest gap. Those rows keep ``rationale IS NULL`` and are
configuration grants by construction (the write path that sets ``granted_by``
did not exist before NEX-074). The check constraint only forbids an *empty*
string, so a future manual repair can add a real reason without a migration.

Revision ID: b4e6f1a2c9d7
Revises: a1c7e2f60b93
Create Date: 2026-11-28 03:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e6f1a2c9d7"
down_revision: str | None = "a1c7e2f60b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "operator_grants"
_COLUMN = "rationale"
_CHECK = "rationale_not_empty"


def upgrade() -> None:
    # batch_alter_table so the same migration runs on SQLite (which cannot ALTER a
    # constraint in place) and PostgreSQL. SQLite rebuilds the table; PostgreSQL
    # alters it. The naming convention on the model gives the constraint a stable
    # name on both, so the downgrade can drop it by name.
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))
        # An empty string is absence pretending to be a record, exactly what the
        # domain value object rejects. NULL stays legal (configuration grants).
        batch.create_check_constraint(
            _CHECK,
            f"{_COLUMN} IS NULL OR length({_COLUMN}) > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_CHECK, type_="check")
        batch.drop_column(_COLUMN)
