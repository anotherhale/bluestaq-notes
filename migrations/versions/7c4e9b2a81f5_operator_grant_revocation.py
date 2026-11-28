"""let an operator grant be revoked

Before this, a grant had exactly one way to end: reach its ``expires_at``. That left
two holes, one of them permanent.

* A **mistaken hand-out** stayed live for its whole term. The remedy was to wait.
* A **configuration grant** (``granted_by IS NULL``) never expires, so it could not be
  ended at all except by editing the deployment's seed and redeploying — which also
  rewrites every other configuration grant, and only takes effect on the next boot.

This adds ``revoked_at`` and ``revoked_by``. A revocation is recorded as a fact on the
existing row rather than applied by deleting it, because the point of keeping a grant is
that an auditor can read what authority existed; a deletion would erase both the grant
and the evidence that it was taken away.

Both columns are nullable and are set together or not at all, enforced by a CHECK. A
revocation with no actor is an anonymous erasure of someone else's authority — the exact
fact an audit must be able to attribute — so it is refused at the schema, not only in
the domain.

``revoked_by`` is a ``SET NULL`` foreign key: revoking a grant is a security fact, and
losing it because the revoker's account was later deleted would erase the record of the
revocation. ``revoked_at`` survives regardless.

No backfill. Existing rows are live as before (``revoked_at IS NULL``); nothing has been
revoked yet, so there is nothing to reconstruct. The index on ``(user_id, scope)`` is
added because revocation is keyed by that pair, but the unique identity of a grant stays
``(user_id, scope, granted_at)`` — one user may hold a scope twice, and both live rows of
that scope are revoked together.

Revision ID: 7c4e9b2a81f5
Revises: 5d2b7c41e9a8
Create Date: 2026-11-28 04:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c4e9b2a81f5"
down_revision: str | None = "5d2b7c41e9a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "operator_grants"
_CHECK = "revocation_is_attributable"


def upgrade() -> None:
    # batch_alter_table so the same migration runs on SQLite (which cannot ALTER to add
    # a constraint in place) and PostgreSQL. SQLite rebuilds the table; PostgreSQL
    # alters it. The naming convention on the model gives the constraint and index
    # stable names on both, so the downgrade can drop them by name.
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("revoked_by", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_operator_grants_revoked_by_users",
            "users",
            ["revoked_by"],
            ["id"],
            ondelete="SET NULL",
        )
        # Set together or not at all. A half-written revocation is not a record.
        batch.create_check_constraint(
            _CHECK,
            "(revoked_at IS NULL AND revoked_by IS NULL) "
            "OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_CHECK, type_="check")
        batch.drop_constraint("fk_operator_grants_revoked_by_users", type_="foreignkey")
        batch.drop_column("revoked_by")
        batch.drop_column("revoked_at")
