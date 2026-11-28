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
trail must not claim a review it cannot evidence. A CHECK constraint is table-wide,
so it cannot be "applied only to new rows": it is evaluated against every existing
row when the table is rebuilt. The constraint therefore has to *tolerate* the one
state legacy data can be in - a version in force carrying no approval at all - while
still refusing a half-written record, which no legitimate writer produces. A version
in force with both columns NULL reads as "no approval on record"
(``PolicyVersion.has_approval_record``); re-publishing it under the new rules
requires re-drafting it with a named approver, which is the correct remediation.
This mirrors the choice made in 9c4d1e8a7b23 for ``drafted_by``.

This revision is edited in place rather than superseded by a follow-up, which needs
justifying now that it has been released (it shipped in the PR that enforced the
published policy at the tool gateway). A follow-up revision cannot repair this
defect at all: the failure happens *during* this revision, so ``upgrade head``
stops here and no later revision is ever reached. Editing in place is also the
only shape that is safe for the databases already carrying it. A database that
applied this revision did so with no legacy in-force row (the predicate refused
them), so it holds no row whose treatment the edit changes; a database that holds
such a row never got past this revision, so it has not run the old form either.
The two populations therefore converge, and neither can be left on a constraint
the other does not have.

Separation of duties here must also survive a legacy row
--------------------------------------------------------
The SoD constraint is replaced in this revision, and the replacement must keep the
tolerance the previous one was given in ``9c4d1e8a7b23``. Writing ``drafted_by IS
NOT NULL`` into any branch of it re-breaks the upgrade for the same reason: a
legacy in-force row has no author, ``NULL IS NOT NULL`` is FALSE, and the table
rebuild that adds ``approved_at``/``approved_by`` then aborts with ``CHECK
constraint failed``. The rule is expressed only as comparisons - ``NULL <> 'x'``
is NULL, which a CHECK passes - so a grandfathered row survives while a *new* row
that collapses author and approver, author and publisher, or approver and
publisher is FALSE and refused. Author and approver *presence* for new rows is
enforced by the domain and ``PublishDraft``, which no legacy row passes through.

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

_SEPARATION_OF_DUTIES_PREVIOUS = "status = 'draft' OR (drafted_by <> published_by)"

# Pairwise-distinct comparisons only. Every ``drafted_by IS NOT NULL`` /
# ``approved_by IS NOT NULL`` guard is deliberately absent: a legacy in-force row
# has both NULL, and ``NULL IS NOT NULL`` is FALSE, so any such guard would make
# the table rebuild below abort with ``CHECK constraint failed``. ``NULL <> 'x'``
# is NULL, which a CHECK passes, so history survives while a new row that
# collapses two roles is refused. Presence for new rows is a domain/use-case rule.
_SEPARATION_OF_DUTIES_NEW = (
    "status = 'draft'"
    " OR (status = 'approved' AND drafted_by <> approved_by)"
    " OR (status IN ('published','retired')"
    " AND drafted_by <> published_by AND drafted_by <> approved_by)"
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
            # A version in force records its publication always, and records an
            # approval when it was published under these rules. The approval is
            # optional *only* so a row that predates this revision can survive the
            # table rebuild; a half-written approval (one column set, the other
            # null) is refused, because no writer produces that.
            " OR (status IN ('published','retired')"
            " AND published_at IS NOT NULL AND published_by IS NOT NULL"
            " AND ((approved_at IS NULL AND approved_by IS NULL)"
            " OR (approved_at IS NOT NULL AND approved_by IS NOT NULL)))",
        )
        batch.create_check_constraint(
            "policy_versions_separation_of_duties", _SEPARATION_OF_DUTIES_NEW
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
