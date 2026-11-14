"""folders and note stars

Adds owner-scoped folders and per-user note stars, plus the ``notes.folder_id``
placement column. Deleting a folder re-homes its notes (the service does this
explicitly); the ``SET NULL`` foreign key is the database-level backstop that
guarantees a note is never orphaned by a folder deletion.

Revision ID: 8b0debcd0ebc
Revises: a7da300eb2d4
Create Date: 2026-11-14 09:40:46.722902
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b0debcd0ebc"
down_revision: str | None = "a7da300eb2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(name) >= 1", name=op.f("ck_folders_name_not_empty")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_folders_owner_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_folders")),
        sa.UniqueConstraint("owner_id", "name", name="uq_folders_owner_id_name"),
    )
    with op.batch_alter_table("folders", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_folders_owner_id"), ["owner_id"], unique=False)

    op.create_table(
        "note_stars",
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["notes.id"],
            name=op.f("fk_note_stars_note_id_notes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_note_stars_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("note_id", "user_id", name=op.f("pk_note_stars")),
    )
    with op.batch_alter_table("note_stars", schema=None) as batch_op:
        batch_op.create_index("ix_note_stars_user_id_note_id", ["user_id", "note_id"], unique=False)

    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("folder_id", sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f("ix_notes_folder_id"), ["folder_id"], unique=False)
        batch_op.create_index(
            "ix_notes_owner_id_folder_id", ["owner_id", "folder_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_notes_folder_id_folders"),
            "folders",
            ["folder_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_notes_folder_id_folders"), type_="foreignkey")
        batch_op.drop_index("ix_notes_owner_id_folder_id")
        batch_op.drop_index(batch_op.f("ix_notes_folder_id"))
        batch_op.drop_column("folder_id")

    with op.batch_alter_table("note_stars", schema=None) as batch_op:
        batch_op.drop_index("ix_note_stars_user_id_note_id")

    op.drop_table("note_stars")
    with op.batch_alter_table("folders", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_folders_owner_id"))

    op.drop_table("folders")
