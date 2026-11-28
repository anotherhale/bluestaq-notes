"""require the approver and the publisher to be different principals

Separation of duties (NEX-050) originally refused only ``author == publisher``,
and NEX-065 added ``author == approver``. Neither covered the third pair: one
operator could approve a draft and then publish it, performing both halves of
the two-person control alone. The domain, the use case and this constraint are
tightened together (NEX-078), because a rule enforced in only some of them is a
rule an alternative code path can walk around.

A legacy in-force row (published before approvals were recorded) carries
``approved_by IS NULL``. ``NULL <> '...'`` evaluates to NULL, which a CHECK
constraint accepts, so the added predicate does not invalidate data written
before the approval column existed - the same property the previous migration
relied on for its own extension. No backfill: there is no approval to invent,
and fabricating one would forge the audit trail the column exists to provide.

Revision ID: 5d2b7c41e9a8
Revises: b4e6f1a2c9d7
"""

from __future__ import annotations

from alembic import op

revision: str = "5d2b7c41e9a8"
down_revision: str | None = "b4e6f1a2c9d7"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE = "policy_versions"
_CONSTRAINT = "policy_versions_separation_of_duties"

# Kept as literals rather than imported from the model: a migration must describe
# the schema as it is at *this* revision, so a later change to the model cannot
# silently rewrite history here.
_THREE_WAY = (
    "status = 'draft'"
    " OR (status = 'approved' AND drafted_by IS NOT NULL AND drafted_by <> approved_by)"
    " OR (status IN ('published','retired') AND drafted_by IS NOT NULL"
    " AND drafted_by <> published_by AND drafted_by <> approved_by"
    " AND approved_by <> published_by)"
)

_PREVIOUS = (
    "status = 'draft'"
    " OR (status = 'approved' AND drafted_by IS NOT NULL AND drafted_by <> approved_by)"
    " OR (status IN ('published','retired') AND drafted_by IS NOT NULL"
    " AND drafted_by <> published_by AND drafted_by <> approved_by)"
)


def upgrade() -> None:
    # Batch mode so SQLite (test suite and local dev) applies the swap through its
    # copy-and-move strategy; the plain ALTER path cannot replace a constraint.
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(_CONSTRAINT, _THREE_WAY)


def downgrade() -> None:
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(_CONSTRAINT, _PREVIOUS)
