"""store governance policy versions so a published policy survives a restart

``InMemoryPolicyVersionRepository`` (NEX-047) proved the port but forgot every
version on restart, including the published ones. A restart therefore emptied the
policy set, and an empty policy set denies everything. That fails closed, so it was
never a security hole, but it meant a deployment could not carry a policy across a
deploy. R41 records the gap; this migration is the durable half.

Two invariants are enforced here rather than only in the use case, because a future
caller that bypasses ``PublishDraft`` must not be able to corrupt policy history:

* ``uq_policy_versions_policy_version`` — ``UNIQUE (policy_id, version_number)``,
  so two authors cannot both claim v4.
* ``uq_policy_versions_one_published`` — a *partial* unique index on
  ``(policy_id) WHERE status = 'published'``, so at most one version of a policy is
  ever in force. Both dialects supported by this project implement partial indexes
  (SQLite since 3.8.0, PostgreSQL always), so the rule is real on both.

No backfill. Policy versions were process-local before this table existed and there
is no earlier table whose rows could reconstruct them. A deployment upgrading with
versions in memory re-publishes them; nothing in the old adapter was durable, so
there is nothing to preserve.

Revision ID: 4b1e7a9c3f28
Revises: f7b3c0d95a12
Create Date: 2026-11-27 13:36:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from notes_service.infrastructure.db.base import UTCDateTime

revision: str = "4b1e7a9c3f28"
down_revision: str | None = "f7b3c0d95a12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "policy_versions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rationale", sa.String(length=400), nullable=False),
        sa.Column("rules", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=True),
        sa.Column("published_at", UTCDateTime(), nullable=True),
        sa.Column("published_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_versions")),
        sa.UniqueConstraint(
            "policy_id", "version_number", name=op.f("uq_policy_versions_policy_version")
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','retired')",
            name=op.f("ck_policy_versions_policy_versions_status_valid"),
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name=op.f("ck_policy_versions_policy_versions_version_positive"),
        ),
        sa.CheckConstraint(
            "length(policy_id) > 0",
            name=op.f("ck_policy_versions_policy_versions_policy_id_not_empty"),
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND published_at IS NULL AND published_by IS NULL)"
            " OR (status IN ('published','retired')"
            " AND published_at IS NOT NULL AND published_by IS NOT NULL)",
            name=op.f("ck_policy_versions_policy_versions_publication_data_consistent"),
        ),
    )
    op.create_index(
        op.f("ix_policy_versions_policy_status"), _TABLE, ["policy_id", "status"], unique=False
    )
    op.create_index(
        "uq_policy_versions_one_published",
        _TABLE,
        ["policy_id"],
        unique=True,
        sqlite_where=sa.text("status = 'published'"),
        postgresql_where=sa.text("status = 'published'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_policy_versions_one_published",
        table_name=_TABLE,
        sqlite_where=sa.text("status = 'published'"),
        postgresql_where=sa.text("status = 'published'"),
    )
    op.drop_index(op.f("ix_policy_versions_policy_status"), table_name=_TABLE)
    op.drop_table(_TABLE)
