"""admit the ``approved`` policy status on a migrated database (NEX-101)

``c4e7a1b93d05`` introduced ``PolicyStatus.APPROVED`` as the state between a
draft and a published version, and taught the *publication* and
*separation-of-duties* constraints about it. It did not touch
``ck_policy_versions_status_valid``, which ``4b1e7a9c3f28`` created as
``status IN ('draft','published','retired')``. That constraint is still on the
table, so on any database built by the migration chain **no row can ever reach
``approved``**: the approve step fails with

    CHECK constraint failed: ck_policy_versions_policy_versions_status_valid

That is not an edge case. Approval is the middle of the only legal path to a
published policy, so on a migrated deployment the entire policy lifecycle is
dead: ``POST /policies/{id}/approve`` cannot succeed, and therefore nothing can
be published, and therefore the governed tool gateway has no policy to evaluate.

Why the test suite never saw it
-------------------------------
``Base.metadata.create_all`` is how the test fixtures build their schema, and
the ORM model carries the *correct* status list. Only ``alembic upgrade``
produces the stale constraint, and the migration tests exercised it only with
``draft``/``published``/``retired`` rows - the same statuses the stale constraint
allows. ``alembic check`` does not report it either: autogenerate compares tables,
columns, indexes and some constraint classes, but it does not diff CHECK
constraint *expressions*. So the drift was invisible to every gate at once.

This revision is a repair rather than an edit of ``c4e7a1b93d05`` because the
released chain already ran on real databases (PR #5 merged): editing a released
revision would leave those databases on the broken constraint while a fresh
install got the fix, which is the worst of both. A new revision upgrades both
paths to the same schema.

The separation-of-duties constraint is recreated here as well, unchanged in
meaning, so that a database which had already applied the repaired
``9c4d1e8a7b23``/``c4e7a1b93d05`` and one which had not both converge on the same
definition. Its predicates are inequality-only - a NULL author or approver passes,
which is what keeps a legacy in-force row readable.

Revision ID: f3a8c1d47e92
Revises: a91c6f0d4b27
Create Date: 2026-11-28 12:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f3a8c1d47e92"
down_revision: str | None = "a91c6f0d4b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "policy_versions"

# Constraint names as *declared* on the model, not as they appear in the database.
# ``NAMING_CONVENTION`` expands ``ck_%(table_name)s_%(constraint_name)s`` on both
# ``drop_constraint`` and ``create_check_constraint``, so passing the short name
# keeps the two in step; passing the already-expanded name would expand it twice
# into ``ck_policy_versions_ck_policy_versions_...`` and drop nothing.
_STATUS_CONSTRAINT = "policy_versions_status_valid"
_SOD_CONSTRAINT = "policy_versions_separation_of_duties"

_STATUSES_WITH_APPROVED = "status IN ('draft','approved','published','retired')"

# The pre-NEX-065 status list, restored by a downgrade. ``approved`` did not exist
# before c4e7a1b93d05, so a narrowed constraint is the honest downgrade shape.
_STATUSES_WITHOUT_APPROVED = "status IN ('draft','published','retired')"

# Inequality-only, and deliberately without any ``IS NOT NULL`` guard: a legacy
# in-force row has a NULL author and approver, and ``NULL IS NOT NULL`` is FALSE,
# so such a guard would abort the rebuild on exactly the rows that must survive.
_SOD = (
    "status = 'draft'"
    " OR (status = 'approved' AND drafted_by <> approved_by)"
    " OR (status IN ('published','retired')"
    " AND drafted_by <> published_by AND drafted_by <> approved_by"
    " AND approved_by <> published_by)"
)


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_STATUS_CONSTRAINT, type_="check")
        batch.create_check_constraint(_STATUS_CONSTRAINT, _STATUSES_WITH_APPROVED)
        batch.drop_constraint(_SOD_CONSTRAINT, type_="check")
        batch.create_check_constraint(_SOD_CONSTRAINT, _SOD)


def downgrade() -> None:
    # ``approved`` is not representable in the old status list, so an approved row
    # has to go somewhere. It is folded to ``draft``: the version was never in
    # force, its rules were never published, and ``draft`` is the only state whose
    # required columns (no approval, no publication) such a row satisfies. Folding
    # it to ``published`` would put unapproved rules in force without a matching
    # publication record, which is the one direction that must never be taken.
    # The approval columns are dropped by c4e7a1b93d05's own downgrade, so nothing
    # is lost here that the older schema could have kept.
    op.execute("UPDATE policy_versions SET status = 'draft' WHERE status = 'approved'")
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_SOD_CONSTRAINT, type_="check")
        batch.create_check_constraint(_SOD_CONSTRAINT, _SOD)
        batch.drop_constraint(_STATUS_CONSTRAINT, type_="check")
        batch.create_check_constraint(_STATUS_CONSTRAINT, _STATUSES_WITHOUT_APPROVED)
