"""let an operator grant's provenance outlive its actor

NEX-079 added ``revoked_at``/``revoked_by`` and a CHECK requiring the pair to be set
together or not at all. NEX-079 also made ``revoked_by`` a ``SET NULL`` foreign key,
on the stated reasoning that a revocation is a security fact which must survive the
deletion of the revoker's account. Those two choices contradict each other, and the
contradiction was latent only because nothing in the application deletes a user yet:

* Deleting an account that had revoked a grant made ``SET NULL`` write
  ``revoked_by = NULL`` onto a row that kept ``revoked_at``, which the CHECK refused.
  The delete did not merely fail — **no account holding revocation history could be
  deleted at all**, and the error surfaced as a raw ``IntegrityError``.
* ``granted_by`` carried the same ``SET NULL`` but no CHECK of its own; instead the
  domain refused a NULL issuer beside a non-NULL ``rationale`` ("a configuration
  operator grant must not carry a rationale"). Deleting an *issuer* therefore left a
  row that raised ``ValidationError`` on load, so every read of the store — the
  operator gate lookup included — failed. That is a self-inflicted outage of the
  whole ``/ops`` surface, triggered by removing an unrelated user.

This migration keeps the ``SET NULL`` semantics the feature actually wants and
replaces the CHECK with the half that is genuinely incoherent: a named revoker with
no instant. The other half (an instant with no surviving actor) is the *correct*
post-deletion state. The domain's matching pair checks are relaxed in the same
commit; the pairing is still enforced where a human supplies it, by
``IssueOperatorGrant`` (rationale required) and the configuration seed (rationale
forbidden).

Downgrade is deliberately lossy-safe rather than data-losing: it restores the strict
CHECK, but only after clearing ``revoked_at`` on rows whose ``revoked_by`` is already
NULL, which are exactly the rows the old constraint could not represent. That is
stated here rather than silently discarding rows.

Revision ID: a91c6f0d4b27
Revises: 7c4e9b2a81f5
Create Date: 2026-11-28 04:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91c6f0d4b27"
down_revision: str | None = "7c4e9b2a81f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "operator_grants"
_OLD_CHECK = "revocation_is_attributable"
_NEW_CHECK = "revocation_actor_requires_instant"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_OLD_CHECK, type_="check")
        # Only the incoherent half: an actor cannot name a revocation that has no
        # instant. A revocation whose actor was later deleted stays representable.
        batch.create_check_constraint(
            _NEW_CHECK,
            "revoked_by IS NULL OR revoked_at IS NOT NULL",
        )


def downgrade() -> None:
    # The strict CHECK cannot represent "revoked, actor since deleted". Rather than
    # delete those audit rows, collapse them to "not revoked": the grant becomes
    # live again, which is the fail-open direction, so it is done explicitly with a
    # comment and only on the rows the old schema could not hold.
    op.execute(
        sa.text(
            "UPDATE operator_grants SET revoked_at = NULL "
            "WHERE revoked_at IS NOT NULL AND revoked_by IS NULL"
        )
    )
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_NEW_CHECK, type_="check")
        batch.create_check_constraint(
            _OLD_CHECK,
            "(revoked_at IS NULL AND revoked_by IS NULL) "
            "OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
        )
