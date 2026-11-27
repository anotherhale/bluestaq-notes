"""organization hierarchy

Adds the organization bounded context: organizations, organization units
(the tenant-bounded hierarchy), teams, and the membership rows that connect
users to organizations and teams.

Tenant isolation is expressed in the schema: every child row carries
``organization_id`` with ``ON DELETE CASCADE`` from ``organizations``, and
self-referencing parent links use ``RESTRICT`` so a unit or team with children
cannot be deleted out from under them. Membership uniqueness is enforced per
(organization, user) and per (team, user), so a user cannot hold two roles in
the same scope.

Revision ID: 189c359206d3
Revises: 8b0debcd0ebc
Create Date: 2026-11-24 00:48:05.498818
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from notes_service.infrastructure.db.base import UTCDateTime

revision: str = "189c359206d3"
down_revision: str | None = "8b0debcd0ebc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','suspended','archived')",
            name=op.f("ck_organizations_status_valid"),
        ),
        sa.CheckConstraint("length(name) >= 1", name=op.f("ck_organizations_name_not_empty")),
        sa.CheckConstraint("length(slug) >= 1", name=op.f("ck_organizations_slug_not_empty")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("slug", name=op.f("uq_organizations_slug")),
    )
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("manager_user_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("ended_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "role IN ('org_owner','org_admin','org_security_admin','org_auditor','org_member')",
            name=op.f("ck_organization_memberships_role_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('active','suspended','deprovisioned')",
            name=op.f("ck_organization_memberships_status_valid"),
        ),
        sa.CheckConstraint(
            "manager_user_id IS NULL OR manager_user_id <> user_id",
            name=op.f("ck_organization_memberships_manager_not_self"),
        ),
        sa.ForeignKeyConstraint(
            ["manager_user_id"],
            ["users.id"],
            name=op.f("fk_organization_memberships_manager_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_organization_memberships_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_organization_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_memberships")),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_organization_memberships_organization_id_user_id"
        ),
    )
    with op.batch_alter_table("organization_memberships", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_organization_memberships_manager_user_id"),
            ["manager_user_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_organization_memberships_org_status", ["organization_id", "status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_organization_memberships_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_organization_memberships_user_id"), ["user_id"], unique=False
        )

    op.create_table(
        "organization_units",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("unit_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','archived')", name=op.f("ck_organization_units_status_valid")
        ),
        sa.CheckConstraint(
            "unit_type IN ('division','department','branch','program')",
            name=op.f("ck_organization_units_unit_type_valid"),
        ),
        sa.CheckConstraint("length(name) >= 1", name=op.f("ck_organization_units_name_not_empty")),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name=op.f("ck_organization_units_parent_not_self"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_organization_units_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["organization_units.id"],
            name=op.f("fk_organization_units_parent_id_organization_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_units")),
        sa.UniqueConstraint(
            "organization_id", "slug", name="uq_organization_units_organization_id_slug"
        ),
    )
    with op.batch_alter_table("organization_units", schema=None) as batch_op:
        batch_op.create_index(
            "ix_organization_units_org_parent", ["organization_id", "parent_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_organization_units_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_organization_units_parent_id"), ["parent_id"], unique=False
        )

    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("parent_team_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint("status IN ('active','archived')", name=op.f("ck_teams_status_valid")),
        sa.CheckConstraint("length(name) >= 1", name=op.f("ck_teams_name_not_empty")),
        sa.CheckConstraint(
            "parent_team_id IS NULL OR parent_team_id <> id", name=op.f("ck_teams_parent_not_self")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_teams_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_team_id"],
            ["teams.id"],
            name=op.f("fk_teams_parent_team_id_teams"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["organization_units.id"],
            name=op.f("fk_teams_unit_id_organization_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("unit_id", "slug", name="uq_teams_unit_id_slug"),
    )
    with op.batch_alter_table("teams", schema=None) as batch_op:
        batch_op.create_index("ix_teams_org_unit", ["organization_id", "unit_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_teams_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_teams_parent_team_id"), ["parent_team_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_teams_unit_id"), ["unit_id"], unique=False)

    op.create_table(
        "team_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "role IN ('team_lead','team_member')", name=op.f("ck_team_memberships_role_valid")
        ),
        sa.CheckConstraint(
            "status IN ('active','suspended','deprovisioned')",
            name=op.f("ck_team_memberships_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_team_memberships_team_id_teams"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_team_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team_memberships")),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_id_user_id"),
    )
    with op.batch_alter_table("team_memberships", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_team_memberships_team_id"), ["team_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_team_memberships_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            "ix_team_memberships_user_id_status", ["user_id", "status"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("team_memberships", schema=None) as batch_op:
        batch_op.drop_index("ix_team_memberships_user_id_status")
        batch_op.drop_index(batch_op.f("ix_team_memberships_user_id"))
        batch_op.drop_index(batch_op.f("ix_team_memberships_team_id"))

    op.drop_table("team_memberships")
    with op.batch_alter_table("teams", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_teams_unit_id"))
        batch_op.drop_index(batch_op.f("ix_teams_parent_team_id"))
        batch_op.drop_index(batch_op.f("ix_teams_organization_id"))
        batch_op.drop_index("ix_teams_org_unit")

    op.drop_table("teams")
    with op.batch_alter_table("organization_units", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_organization_units_parent_id"))
        batch_op.drop_index(batch_op.f("ix_organization_units_organization_id"))
        batch_op.drop_index("ix_organization_units_org_parent")

    op.drop_table("organization_units")
    with op.batch_alter_table("organization_memberships", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_organization_memberships_user_id"))
        batch_op.drop_index(batch_op.f("ix_organization_memberships_organization_id"))
        batch_op.drop_index("ix_organization_memberships_org_status")
        batch_op.drop_index(batch_op.f("ix_organization_memberships_manager_user_id"))

    op.drop_table("organization_memberships")
    op.drop_table("organizations")
