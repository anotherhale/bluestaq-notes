"""anchor each agent's audit chain outside its entries

The hash chain in ``tool_invocations`` proves that the entries it *contains* are
intact and contiguous. It cannot prove how many entries there should be, so
deleting the newest rows leaves a shorter chain that verifies cleanly — and the
next invocation, which linked off the greatest surviving row, re-anchored the
chain on the truncated tail and healed the evidence permanently.

This migration adds the high-water mark that closes that hole: two columns on
``agents`` recording the sequence and intent hash of the last entry ever written
for that agent. New entries link to the anchor rather than to the greatest
surviving row, and the verifier requires the trail's tail to match it.

Existing agents are backfilled from their current chains, so no live trail is
reported as truncated by the migration itself. An agent with no invocations is
left at ``(0, GENESIS_HASH)``, which the verifier treats as "no anchor to check".

Revision ID: c7a91e3d5b62
Revises: f19a2c6d8b04
Create Date: 2026-11-27 09:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7a91e3d5b62"
down_revision: str | None = "f19a2c6d8b04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GENESIS_HASH = "0" * 64


def upgrade() -> None:
    # Added nullable first, backfilled, then made NOT NULL: a NOT NULL column
    # added without a server default would fail on a populated ``agents`` table.
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("chain_anchor_sequence", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("chain_anchor_hash", sa.String(length=64), nullable=True))

    bind = op.get_bind()
    invocations = sa.table(
        "tool_invocations",
        sa.column("agent_id", sa.Uuid()),
        sa.column("chain_sequence", sa.Integer()),
        sa.column("intent_hash", sa.String()),
    )
    agents = sa.table(
        "agents",
        sa.column("id", sa.Uuid()),
        sa.column("chain_anchor_sequence", sa.Integer()),
        sa.column("chain_anchor_hash", sa.String()),
    )

    for agent_id in [row[0] for row in bind.execute(sa.select(agents.c.id)).fetchall()]:
        tail = bind.execute(
            sa.select(invocations.c.chain_sequence, invocations.c.intent_hash)
            .where(invocations.c.agent_id == agent_id)
            .order_by(invocations.c.chain_sequence.desc())
            .limit(1)
        ).first()
        bind.execute(
            agents.update()
            .where(agents.c.id == agent_id)
            .values(
                chain_anchor_sequence=tail[0] if tail is not None else 0,
                chain_anchor_hash=tail[1] if tail is not None else _GENESIS_HASH,
            )
        )

    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.alter_column(
            "chain_anchor_sequence",
            existing_type=sa.Integer(),
            nullable=False,
            server_default="0",
        )
        batch_op.alter_column(
            "chain_anchor_hash",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default=_GENESIS_HASH,
        )


def downgrade() -> None:
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_column("chain_anchor_hash")
        batch_op.drop_column("chain_anchor_sequence")


__all__ = ["downgrade", "upgrade"]
