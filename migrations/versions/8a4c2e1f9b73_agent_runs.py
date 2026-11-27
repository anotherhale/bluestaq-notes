"""durable agent runs: an ordered plan of governed tool steps

Adds ``agent_runs`` and ``agent_run_steps``. A run is the durable sequence around
the governed tool gateway: each step records the arguments it was asked to
perform, so a resumed run replays the plan instead of re-deriving it.

Two deliberate differences from ``tool_invocations``:

* Step ``arguments`` are stored, not just their digest. Durability needs the
  inputs. The row is owner-scoped working state, not a broadly readable audit
  trail; the audit trail remains ``tool_invocations``.
* ``(run_id, sequence)`` is unique, so a step cannot be duplicated by a retried
  writer, and ``status`` is constrained to the step lifecycle.

Revision ID: 8a4c2e1f9b73
Revises: 5d0b71f8ac34
Create Date: 2026-11-25 04:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from notes_service.infrastructure.db.base import UTCDateTime

revision: str = "8a4c2e1f9b73"
down_revision: str | None = "5d0b71f8ac34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("delegation_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("finished_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','interrupted','completed','failed','aborted')",
            name=op.f("ck_agent_runs_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_runs_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["delegation_id"],
            ["agent_delegations.id"],
            name=op.f("fk_agent_runs_delegation_id_agent_delegations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_table(
        "agent_run_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("arguments_digest", sa.String(length=64), nullable=False),
        sa.Column("arguments", sa.Text(), nullable=False),
        sa.Column("resource_ref", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=500), nullable=True),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("completed_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','started','succeeded','failed','interrupted')",
            name=op.f("ck_agent_run_steps_status_valid"),
        ),
        sa.CheckConstraint(
            "length(arguments_digest) = 64",
            name=op.f("ck_agent_run_steps_digest_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_agent_run_steps_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_run_steps")),
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name=op.f("uq_agent_run_steps_run_sequence"),
        ),
    )
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_runs_agent_id"), ["agent_id"], unique=False)
        batch_op.create_index("ix_agent_runs_agent_status", ["agent_id", "status"], unique=False)
    with op.batch_alter_table("agent_run_steps", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_run_steps_run_id"), ["run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("agent_run_steps", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_run_steps_run_id"))
    op.drop_table("agent_run_steps")
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_agent_runs_agent_status")
        batch_op.drop_index(batch_op.f("ix_agent_runs_agent_id"))
    op.drop_table("agent_runs")
