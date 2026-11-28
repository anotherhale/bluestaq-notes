"""record the drafting principal and enforce separation of duties (NEX-050)

NEX-047 shipped the policy lifecycle with a publisher but no author, so nothing
stopped the operator who drafted a version from publishing it. M3.3 requires the
opposite: the security architecture says the actor requesting a sensitive action
cannot approve it, and putting a policy in force for every agent is the most
sensitive action the platform has. This migration adds the author and the rule.

Two changes:

* ``drafted_by`` is added **nullable**, because a row written before this
  migration has no recorded author. A ``NOT NULL`` column would fail on an
  existing deployment with rows in it.
* ``ck_policy_versions_separation_of_duties`` refuses a non-draft row whose
  author equals its publisher. It is a schema rule and not only a use-case rule
  so a future caller that bypasses ``PublishDraft`` cannot put a self-published
  policy in force.

Why the constraint does **not** require ``drafted_by IS NOT NULL``
------------------------------------------------------------------
The first version of this migration did require it, and that made ``upgrade
head`` impossible for any deployment that had published a policy before this
revision existed. A CHECK is table-wide: SQLite rebuilds the table to add the
column, and PostgreSQL revalidates the table, so the predicate runs against every
*existing* row. A legacy published row carries ``drafted_by = NULL``, and
``NULL IS NOT NULL`` is FALSE, not NULL - so the row failed the rebuild and the
whole upgrade aborted with ``CHECK constraint failed``. The docstring promised
the row "remains readable" while the constraint made it unsafe to even reach that
state.

The rule is therefore expressed only as comparisons, which is how the
``approved_by`` pair is handled at ``c4e7a1b93d05``: ``NULL <> 'x'`` evaluates to
NULL, and a CHECK treats NULL as satisfied rather than violated. A legacy row
(with a NULL author) passes; a *new* row whose author equals its publisher is
FALSE and is refused. Author *presence* for anything in force is enforced where a
NULL cannot arise from history - the domain (``separation_of_duties`` refuses an
unspecified author) and ``PublishDraft`` - because a table-wide CHECK cannot
distinguish a grandfathered row from a malformed new one, and relaxing it to
``drafted_by IS NOT NULL`` is what broke the upgrade.

No backfill. A pre-existing *published* row has no author to record, and
inventing one (the publisher, or a placeholder) would defeat the control for
exactly the rows that most need it. Such a row is left as-is and remains
readable; re-publishing it under the new rules requires re-drafting it with a
named author, which is the correct remediation. This is called out in the
migration report rather than hidden behind a silent UPDATE.

This revision is edited in place rather than superseded by a follow-up: the
failure happens *during* this revision, so ``upgrade head`` would still stop here
and a later revision could never run. Editing it only affects databases that have
not reached it yet; one that did (necessarily with no legacy in-force row, since
the predicate refused them) is unaffected.

Revision ID: 9c4d1e8a7b23
Revises: 4b1e7a9c3f28
Create Date: 2026-11-27 14:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c4d1e8a7b23"
down_revision: str | None = "4b1e7a9c3f28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "policy_versions"


def upgrade() -> None:
    # batch_alter_table so the same migration runs on SQLite (which cannot ALTER a
    # constraint in place) and PostgreSQL. SQLite rebuilds the table; PostgreSQL
    # alters it. The naming convention on the model is what gives the constraint a
    # stable name on both.
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("drafted_by", sa.String(length=64), nullable=True))
        # Comparisons only, deliberately without ``drafted_by IS NOT NULL``: a
        # legacy row's NULL author must survive the table rebuild, and a NULL
        # comparison passes a CHECK. Author *presence* for new rows is enforced by
        # the domain and the publish use case. See the module docstring.
        batch.create_check_constraint(
            "policy_versions_separation_of_duties",
            "status = 'draft' OR (drafted_by <> published_by)",
        )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint("policy_versions_separation_of_duties", type_="check")
        batch.drop_column("drafted_by")
