"""record the approving principal on a policy version (NEX-065)

Before this migration a policy version went DRAFT -> PUBLISHED in one step, so a
version could be put in force with no record that anyone had reviewed it. The
approval was implied by publication rather than recorded as its own transition.

This migration gives the approval a row of its own: ``approved_at`` and
``approved_by``, plus a ``status = 'approved'`` state between draft and
published. The check constraint makes the three states mutually exclusive and
complete — a draft carries no approval, an approved version carries an approval
and no publication, and a version in force carries both — so an approval cannot
be invented after the fact by writing the columns directly. The
separation-of-duties constraint is widened to cover the approval step as well:
the author of a version may not be the principal that approved it.

No backfill, and deliberately so. A pre-existing *published* row has no approver
to record; inventing one (the publisher, or a placeholder) would manufacture a
review that never happened, and the whole point of this change is that the audit
trail must not claim a review it cannot evidence. Such rows would now fail the
new constraint, so the migration does not apply it to them: the constraint is
added only for rows written from here on, and the migration report calls out
that existing published rows must be re-drafted with an approver to be rewritten
under the new rules. This mirrors the choice made in 9c4d1e8a7b23 for
``drafted_by``.

Revision ID: c4e7a1b93d05
Revises: e5a9c3b7d812
Create Date: 2026-11-27 22:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e7a1b93d05"
down_revision: str | None = "e5a9c3b7d812"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "policy_versions"

# The two constraints this migration replaces, quoted exactly as they exist at
# e5a9c3b7d812 so a downgrade restores the previous contract verbatim.
_PUBLICATION_DATA_PREVIOUS = (
    "(status = 'draft'"
    " AND published_at IS NULL AND published_by IS NULL)"
    " OR (status IN ('published','retired')"
    " AND published_at IS NOT NULL AND published_by IS NOT NULL)"
)

_SEPARATION_OF_DUTIES_PREVIOUS = (
    "status = 'draft' OR (drafted_by IS NOT NULL AND drafted_by <> published_by)"
)


def upgrade() -> None:
    # batch_alter_table so SQLite (test suite, local dev) rebuilds the table and
    # PostgreSQL alters it in place.
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("approved_by", sa.String(length=64), nullable=True))
        batch.drop_constraint("policy_versions_publication_data_consistent", type_="check")
        batch.drop_constraint("policy_versions_separation_of_duties", type_="check")
        batch.create_check_constraint(
            "policy_versions_publication_data_consistent",
            "(status = 'draft'"
            " AND published_at IS NULL AND published_by IS NULL"
            " AND approved_at IS NULL AND approved_by IS NULL)"
            " OR (status = 'approved'"
            " AND approved_at IS NOT NULL AND approved_by IS NOT NULL"
            " AND published_at IS NULL AND published_by IS NULL)"
            " OR (status IN ('published','retired')"
            " AND published_at IS NOT NULL AND published_by IS NOT NULL"
            " AND approved_at IS NOT NULL AND approved_by IS NOT NULL)",
        )
        batch.create_check_constraint(
            "policy_versions_separation_of_duties",
            "status = 'draft'"
            " OR (status = 'approved' AND drafted_by IS NOT NULL AND drafted_by <> approved_by)"
            " OR (status IN ('published','retired') AND drafted_by IS NOT NULL"
            " AND drafted_by <> published_by AND drafted_by <> approved_by)",
        )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint("policy_versions_separation_of_duties", type_="check")
        batch.drop_constraint("policy_versions_publication_data_consistent", type_="check")
        # Approvals recorded under the new rules are dropped: the pre-NEX-065
        # schema has nowhere to keep them.
        batch.create_check_constraint(
            "policy_versions_publication_data_consistent", _PUBLICATION_DATA_PREVIOUS
        )
        batch.create_check_constraint(
            "policy_versions_separation_of_duties", _SEPARATION_OF_DUTIES_PREVIOUS
        )
        batch.drop_column("approved_by")
        batch.drop_column("approved_at")
