"""chain the tool-invocation audit trail

Adds the four columns that make ``tool_invocations`` a tamper-evident hash chain
per agent: ``chain_sequence`` (1-based position), ``previous_hash`` (the prior
entry's ``intent_hash``), ``intent_hash`` (seal over the immutable attempt), and
``outcome_hash`` (seal over the terminal verdict).

Existing rows are backfilled rather than left null. A null chain on old rows
would be indistinguishable from a deleted chain, which is exactly the finding
the verifier is supposed to raise — so the migration computes the real hashes
over the rows already present, in ``invoked_at`` order per agent. The backfill
is deterministic (it uses each row's stored fields, not the clock), so running
the migration twice on the same data would produce the same chain; but
``upgrade`` itself runs once per database by Alembic's own accounting.

The columns are added nullable, backfilled, then made NOT NULL. That order is
what keeps the migration safe on a populated table: a NOT NULL column added
without a server default would fail immediately.

Revision ID: b2e5c81f4a07
Revises: 8a4c2e1f9b73
Create Date: 2026-11-25 05:35:00.000000
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from notes_service.infrastructure.db.base import UTCDateTime

revision: str = "b2e5c81f4a07"
down_revision: str | None = "8a4c2e1f9b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GENESIS_HASH = "0" * 64


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


def _compute_hashes(row: object, previous_hash: str) -> tuple[str, str | None]:
    """Recompute one row's hashes exactly as the domain module does.

    Kept inline rather than imported from ``domain.agent.audit_chain`` because a
    migration must stay reproducible even if that module is later refactored;
    it pins the v1 formula it backfilled with.
    """
    invoked_at = row.invoked_at
    intent = _sha256(
        _canonical(
            {
                "v": 1,
                "kind": "tool_invocation.intent",
                "id": str(row.id),
                "agent_id": str(row.agent_id),
                "token_id": str(row.token_id) if row.token_id is not None else None,
                "tool_name": row.tool_name,
                "capability": row.capability,
                "idempotency_key": row.idempotency_key,
                "arguments_digest": row.arguments_digest,
                "resource_ref": row.resource_ref,
                "approval_request_id": (
                    str(row.approval_request_id) if row.approval_request_id is not None else None
                ),
                "invoked_at": _timestamp(invoked_at),
                "previous_hash": previous_hash,
            }
        )
    )
    if row.completed_at is None:
        return intent, None
    return intent, _sha256(
        _canonical(
            {
                "v": 1,
                "kind": "tool_invocation.outcome",
                "intent_hash": intent,
                "status": row.status,
                "outcome": row.outcome,
                "completed_at": _timestamp(row.completed_at),
            }
        )
    )


def upgrade() -> None:
    with op.batch_alter_table("tool_invocations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("chain_sequence", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("previous_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("intent_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("outcome_hash", sa.String(length=64), nullable=True))

    bind = op.get_bind()
    table = sa.table(
        "tool_invocations",
        sa.column("id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("token_id", sa.Uuid()),
        sa.column("tool_name", sa.String()),
        sa.column("capability", sa.String()),
        sa.column("idempotency_key", sa.String()),
        sa.column("arguments_digest", sa.String()),
        sa.column("resource_ref", sa.String()),
        sa.column("status", sa.String()),
        sa.column("outcome", sa.String()),
        sa.column("approval_request_id", sa.Uuid()),
        sa.column("invoked_at", UTCDateTime()),
        sa.column("completed_at", UTCDateTime()),
    )

    agent_ids = [row[0] for row in bind.execute(sa.select(table.c.agent_id).distinct()).fetchall()]
    for agent_id in agent_ids:
        rows = bind.execute(
            sa.select(
                table.c.id,
                table.c.agent_id,
                table.c.token_id,
                table.c.tool_name,
                table.c.capability,
                table.c.idempotency_key,
                table.c.arguments_digest,
                table.c.resource_ref,
                table.c.status,
                table.c.outcome,
                table.c.approval_request_id,
                table.c.invoked_at,
                table.c.completed_at,
            )
            .where(table.c.agent_id == agent_id)
            .order_by(table.c.invoked_at.asc(), table.c.id.asc())
        ).fetchall()
        previous = _GENESIS_HASH
        for sequence, row in enumerate(rows, start=1):
            intent, outcome = _compute_hashes(row, previous)
            bind.execute(
                table.update()
                .where(table.c.id == row.id)
                .values(
                    chain_sequence=sequence,
                    previous_hash=previous,
                    intent_hash=intent,
                    outcome_hash=outcome,
                )
            )
            previous = intent

    # Any agent with no rows needs nothing; agents with rows are now fully
    # chained. A row that somehow escaped the loop would block NOT NULL below,
    # which is the safe failure: better to abort the migration than to commit a
    # half-chained trail.
    with op.batch_alter_table("tool_invocations", schema=None) as batch_op:
        batch_op.alter_column("chain_sequence", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("previous_hash", existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column("intent_hash", existing_type=sa.String(length=64), nullable=False)
        batch_op.create_unique_constraint(
            op.f("uq_tool_invocations_agent_chain_sequence"),
            ["agent_id", "chain_sequence"],
        )


def downgrade() -> None:
    with op.batch_alter_table("tool_invocations", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("uq_tool_invocations_agent_chain_sequence"), type_="unique")
        batch_op.drop_column("outcome_hash")
        batch_op.drop_column("intent_hash")
        batch_op.drop_column("previous_hash")
        batch_op.drop_column("chain_sequence")


__all__ = ["downgrade", "upgrade"]
