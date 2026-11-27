"""record the drafting principal and enforce separation of duties (NEX-050)

NEX-047 shipped the policy lifecycle with a publisher but no author, so nothing
stopped the operator who drafted a version from publishing it. M3.3 requires the
opposite: the security architecture says the actor requesting a sensitive action
cannot approve it, and putting a policy in force for every agent is the most
sensitive action the platform has. This migration adds the author and the rule.

Two changes, and the ordering matters:

* ``drafted_by`` is added **nullable**, because a row written before this
  migration has no recorded author. A ``NOT NULL`` column would fail on an
  existing deployment with rows in it. The check constraint below is what makes
  the nullability safe: a draft may have no author, but nothing *in force* may
  lack one, so a legacy published row is rejected the next time it is written
  rather than silently trusted.
* ``ck_policy_versions_separation_of_duties`` refuses any non-draft row whose
  author is null or equal to its publisher. It is a schema rule and not only a
  use-case rule so a future caller that bypasses ``PublishDraft`` cannot put a
  self-published policy in force.

No backfill. A pre-existing *published* row has no author to record, and
inventing one (the publisher, or a placeholder) would defeat the control for
exactly the rows that most need it. Such a row is left as-is and remains
readable; re-publishing it under the new rules requires re-drafting it with a
named author, which is the correct remediation. This is called out in the
migration report rather than hidden behind a silent UPDATE.

Revision ID: 9c4d1e8a7b23
Revises: 4b1e7a9c3f28
Create Date: 2026-11-27 14:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c4d1e8a7b23"
down_revision: str | None = "4b1e7a9c3f28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "policy_versions"


def upgrade() -> None:
    # batch_alter_table so the same migration runs on SQLite (which cannot ALTER a
    # constraint in place) and PostgreSQL. SQLite rebuilds the table; PostgreSQL
    # alters it. The naming convention on the model is what gives the constraint a
    # stable name on both.
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("drafted_by", sa.String(length=64), nullable=True))
        batch.create_check_constraint(
            "policy_versions_separation_of_duties",
            "status = 'draft' OR (drafted_by IS NOT NULL AND drafted_by <> published_by)",
        )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint("policy_versions_separation_of_duties", type_="check")
        batch.drop_column("drafted_by")
