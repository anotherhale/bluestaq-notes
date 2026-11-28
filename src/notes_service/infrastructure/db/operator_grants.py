"""Durable operator grant store (NEX-046), closing R40.

The in-memory adapter proved the port; this one makes a grant outlive the
process. R40 recorded the gap precisely: a restart forgot every bounded grant,
and each worker in a multi-worker deployment held its own copy, so a grant issued
on one worker was invisible to the others. It failed closed, so it was never a
security regression — but a grant nobody can see is a grant nobody can audit,
which is the question NEX-045 exists to answer.

Nothing above this file changes. ``OperatorGate`` and ``ListOperatorGrants``
depend on :class:`~notes_service.application.ports.operator_grants.OperatorGrantRepository`,
not on a store, and that is the whole reason the port exists.

Two properties the port promises, and how they survive a table
--------------------------------------------------------------

* **Insertion order.** ``active_grants`` must be stable for a given store state.
  A surrogate autoincrement ``id`` is the only portable way to recover insertion
  order, so ordering is by ``id`` and never by ``granted_at`` — two grants in one
  seed share a timestamp, and sorting by it would make the answer
  implementation-defined. This is also why ``id`` exists rather than a composite
  primary key.
* **Idempotent ``add``.** ``(user_id, scope, granted_at)`` is unique, and ``add``
  updates the existing row in place. A re-issued grant (same identity, new
  ``expires_at``) therefore extends the deadline rather than creating a second
  row, and a seed that runs on every boot cannot accumulate one grant per restart.

A session, not a unit of work
-----------------------------

Unlike the content repositories, this adapter takes a ``Session`` directly rather
than participating in a :class:`~notes_service.infrastructure.db.uow.SqlAlchemyUnitOfWork`.
A grant is not part of a content transaction: the gate reads it on every
platform-wide request, including ones that open no unit of work at all, and the
seed path writes it at startup before any request exists. Giving it a session of
its own keeps a privileged read from opening (and holding) a content
transaction just to answer "may this caller read this view".
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from notes_service.domain.identity.operator_grant import OperatorGrant, OperatorGrantRationale
from notes_service.domain.identity.value_objects import UserId
from notes_service.infrastructure.db.models import OperatorGrantRow


def _as_uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


def _to_domain(row: OperatorGrantRow) -> OperatorGrant:
    return OperatorGrant(
        user_id=UserId(str(row.user_id)),
        scope=row.scope,
        granted_at=row.granted_at,
        granted_by=UserId(str(row.granted_by)) if row.granted_by is not None else None,
        not_before=row.not_before,
        expires_at=row.expires_at,
        # A stored rationale is re-validated on the way out, exactly like a scope:
        # the value object is the single definition of "a valid reason", so a row
        # written before a bound changed (or by hand) fails loudly here rather
        # than surfacing as an unchecked string.
        rationale=(OperatorGrantRationale(row.rationale) if row.rationale is not None else None),
        revoked_at=row.revoked_at,
        revoked_by=UserId(str(row.revoked_by)) if row.revoked_by is not None else None,
    )


class SqlAlchemyOperatorGrantRepository:
    """Table-backed operator grant store, safe across restarts and workers."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "sqlalchemy"

    def add(self, grant: OperatorGrant) -> None:
        if not isinstance(grant, OperatorGrant):
            raise TypeError("grant must be an OperatorGrant")
        with self._session_factory() as session:
            existing = session.execute(
                select(OperatorGrantRow).where(
                    OperatorGrantRow.user_id == _as_uuid(grant.user_id),
                    OperatorGrantRow.scope == grant.scope,
                    OperatorGrantRow.granted_at == grant.granted_at,
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    OperatorGrantRow(
                        user_id=_as_uuid(grant.user_id),
                        scope=grant.scope,
                        granted_at=grant.granted_at,
                        granted_by=(
                            _as_uuid(grant.granted_by) if grant.granted_by is not None else None
                        ),
                        not_before=grant.not_before,
                        expires_at=grant.expires_at,
                        rationale=(grant.rationale.value if grant.rationale is not None else None),
                    )
                )
            else:
                existing.granted_by = (
                    _as_uuid(grant.granted_by) if grant.granted_by is not None else None
                )
                existing.not_before = grant.not_before
                existing.expires_at = grant.expires_at
                existing.rationale = grant.rationale.value if grant.rationale is not None else None
            session.commit()

    def replace_configured(self, grants: Iterable[OperatorGrant]) -> None:
        materialised = tuple(grants)
        for grant in materialised:
            if not isinstance(grant, OperatorGrant):
                raise TypeError("grant must be an OperatorGrant")
            if grant.granted_by is not None:
                raise ValueError("replace_configured only accepts configuration grants")
        with self._session_factory() as session:
            # Delete only configuration grants that are still live. A revoked
            # configuration grant is kept (NEX-079): the revocation is a standing
            # decision, and deleting the row here would let the next boot's seed
            # silently reinstate authority an operator deliberately removed.
            live_config = [
                row
                for row in session.execute(
                    select(OperatorGrantRow).where(OperatorGrantRow.granted_by.is_(None))
                ).scalars()
                if row.revoked_at is None
            ]
            for row in live_config:
                session.delete(row)
            # A scope that was revoked for this user stays revoked: skip its re-seed
            # so the same boot cannot add a fresh live grant beside the revoked one.
            revoked_pairs = {
                (row.user_id, row.scope)
                for row in session.execute(
                    select(OperatorGrantRow).where(
                        OperatorGrantRow.granted_by.is_(None),
                        OperatorGrantRow.revoked_at.is_not(None),
                    )
                ).scalars()
            }
            session.add_all(
                [
                    OperatorGrantRow(
                        user_id=_as_uuid(grant.user_id),
                        scope=grant.scope,
                        granted_at=grant.granted_at,
                        granted_by=None,
                        not_before=grant.not_before,
                        expires_at=grant.expires_at,
                    )
                    for grant in materialised
                    if (_as_uuid(grant.user_id), grant.scope) not in revoked_pairs
                ]
            )
            session.commit()

    def active_for(self, user_id: UserId, *, now: datetime) -> frozenset[str]:
        with self._session_factory() as session:
            rows = session.execute(
                select(OperatorGrantRow)
                .where(OperatorGrantRow.user_id == _as_uuid(user_id))
                .order_by(OperatorGrantRow.id)
            ).scalars()
            # ``is_active`` is the domain's decision, so expiry is evaluated in
            # exactly one place regardless of which adapter answered. Filtering in
            # SQL would duplicate the rule and let the two drift.
            return frozenset(row.scope for row in rows if _to_domain(row).is_active(now=now))

    def revoke(self, user_id: UserId, scope: str, *, at: datetime, by: UserId) -> int:
        """Stamp a revocation on every live grant of ``scope`` (NEX-079).

        ``is_active`` is the domain's decision, so the rows that count as "live" are
        selected here and filtered through it rather than through a SQL predicate on
        ``expires_at`` — one definition of "in force", the same one the gate uses.
        The update itself is still a single statement per row, and it only writes
        the two revocation columns: the grant's original bound and provenance are
        never rewritten by a revocation.

        A row already carrying a revocation is skipped, so the *first* revocation is
        the one recorded and a repeat call reports zero (see the port). The commit
        happens once, after every matching row is stamped, so a partial revocation
        cannot be observed.
        """
        changed = 0
        with self._session_factory() as session:
            candidates = session.execute(
                select(OperatorGrantRow)
                .where(
                    OperatorGrantRow.user_id == _as_uuid(user_id),
                    OperatorGrantRow.scope == scope,
                )
                .order_by(OperatorGrantRow.id)
            ).scalars()
            for row in candidates:
                if row.revoked_at is not None:
                    continue
                if not _to_domain(row).is_active(now=at):
                    continue
                row.revoked_at = at
                row.revoked_by = _as_uuid(by)
                changed += 1
            session.commit()
        return changed

    def active_grants(self, *, now: datetime) -> tuple[OperatorGrant, ...]:
        with self._session_factory() as session:
            rows = session.execute(select(OperatorGrantRow).order_by(OperatorGrantRow.id)).scalars()
            return tuple(grant for row in rows if (grant := _to_domain(row)).is_active(now=now))

    def revoked_grants(
        self, *, limit: int | None = None, offset: int = 0
    ) -> tuple[OperatorGrant, ...]:
        """Filter, order and bound in the store (NEX-092).

        NEX-086 recorded that this read was bounded and it was not: the selection
        ran in Python over every grant row — ``select(OperatorGrantRow)`` with no
        ``WHERE``, a domain object built for each — and only then sliced the
        materialised list. A page of 50 therefore cost the whole table, which is
        the unbounded-response defect NEX-087 fixed for the access log and NEX-088
        for the audit trail, one layer down in the adapter.

        NEX-086 kept the predicate in Python to avoid a SQL translation drifting
        from ``is_revoked``. The premise does not hold: ``is_revoked`` is a single
        attribute test, ``self.revoked_at is not None``, with no logic in it, so
        ``revoked_at IS NOT NULL`` is its exact translation rather than an
        approximation of it. There is nothing to drift, and the ``CHECK`` on the
        table forbids the only incoherent half (a named revoker with no instant).
        ``is_active`` does carry real logic — a clock and two nullable deadlines —
        which is why that filter stays in Python above; this one does not.

        ``id`` ordering is the port's insertion order, and it is also what makes
        ``LIMIT``/``OFFSET`` here stable: the list is never appended to by reading
        it, so a page cannot shift under a walker.
        """
        statement = (
            select(OperatorGrantRow)
            .where(OperatorGrantRow.revoked_at.is_not(None))
            .order_by(OperatorGrantRow.id)
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self._session_factory() as session:
            rows = session.execute(statement).scalars()
            return tuple(_to_domain(row) for row in rows)

    def count_revoked(self) -> int:
        """How many grants have been revoked, ever — an aggregate (NEX-086, NEX-092).

        A paged report needs the total to say whether a page is the last one and
        to page without the caller knowing the size in advance. NEX-086 counted by
        materialising every row and filtering in Python, so the "without loading
        them" promise in the port was not kept either. ``count(*)`` over
        ``revoked_at IS NOT NULL`` reads no rows and cannot be truncated by volume,
        and it selects through the same predicate as ``revoked_grants`` — the
        ``WHERE`` clause is written once, in the adapter, beside the one it must
        agree with.
        """
        with self._session_factory() as session:
            total = session.execute(
                select(func.count())
                .select_from(OperatorGrantRow)
                .where(OperatorGrantRow.revoked_at.is_not(None))
            ).scalar_one()
        return int(total)


__all__ = ["SqlAlchemyOperatorGrantRepository"]
