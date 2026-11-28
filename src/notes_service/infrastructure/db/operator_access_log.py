"""Durable operator access log (NEX-072), closing R42.

The in-memory adapter proved the port; this one makes the record outlive the
process. R42 is the gap: the log that answers "who read a platform-wide view"
lived in a process-local ring, so it was per-worker and cleared on restart. In a
deployment with more than one worker, an operator reading the report saw only the
reads that worker had answered, and a restart erased the rest. The record that
exists *because* a privileged read must be auditable was the one record a restart
could delete.

Nothing above this file changes. ``OperatorGate`` and ``GetOperatorAccessReport``
depend on :class:`~notes_service.application.ports.operator_access.OperatorAccessLog`,
not on a store — the same reason the port exists at all.

Two properties the port promises, and how they survive a table
--------------------------------------------------------------

* **Newest first.** ``recent`` must return entries in reverse insertion order. A
  surrogate autoincrement ``id`` is the only portable way to recover insertion
  order, so ordering is by ``id`` and never by ``at`` — two reads can share a
  timestamp, and sorting by it would make the answer implementation-defined.
* **Append-only.** There is no update and no delete path. ``record`` inserts one
  row. The record is evidence of an authorised read; a store that could rewrite it
  would not be evidence.

A session, not a unit of work
-----------------------------

Like the grant store, this adapter takes a ``Session`` directly rather than
participating in a :class:`~notes_service.infrastructure.db.uow.SqlAlchemyUnitOfWork`.
The gate records the read on the request path, and that read must land even if the
caller's content transaction later rolls back — the fact that an operator *saw*
the view happened regardless of whether their own write succeeded.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.ports.operator_access import OperatorAccessEntry
from notes_service.domain.identity.value_objects import UserId
from notes_service.infrastructure.db.models import OperatorAccessRow


def _as_uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


def _to_domain(row: OperatorAccessRow) -> OperatorAccessEntry:
    return OperatorAccessEntry(
        user_id=UserId(str(row.user_id)),
        operation=row.operation,
        at=row.at,
    )


class SqlAlchemyOperatorAccessLog:
    """Table-backed, append-only operator access log, safe across restarts."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "sqlalchemy"

    def record(self, entry: OperatorAccessEntry) -> None:
        if not isinstance(entry, OperatorAccessEntry):
            raise TypeError("entry must be an OperatorAccessEntry")
        with self._session_factory() as session:
            session.add(
                OperatorAccessRow(
                    user_id=_as_uuid(entry.user_id),
                    operation=entry.operation,
                    at=entry.at,
                )
            )
            session.commit()

    def recent(self, *, limit: int) -> tuple[OperatorAccessEntry, ...]:
        if limit < 1:
            return ()
        with self._session_factory() as session:
            rows = session.execute(
                select(OperatorAccessRow).order_by(OperatorAccessRow.id.desc()).limit(limit)
            ).scalars()
            return tuple(_to_domain(row) for row in rows)

    def purge_before(self, *, cutoff: datetime) -> int:
        """Delete rows stamped strictly before ``cutoff``; return how many went.

        Strictly before, so a row exactly at the cutoff survives — the policy's
        window is inclusive at its near edge and this must match it. The delete is
        a single statement rather than a read-then-delete loop, so a row inserted
        concurrently with a later timestamp is never in the candidate set, and the
        count returned is the database's own ``rowcount`` rather than a guess.

        This is the only mutation this adapter permits. There is no update path and
        no way to delete a row that is inside the window: a store that could
        rewrite or selectively erase the record of a privileged read would not be
        evidence of anything.
        """
        with self._session_factory() as session:
            result = session.execute(delete(OperatorAccessRow).where(OperatorAccessRow.at < cutoff))
            session.commit()
            # ``rowcount`` is on the CursorResult a DML statement returns, but the
            # typed signature of ``execute`` is the generic Result. Reading it
            # defensively keeps a driver that omits it from turning a successful
            # purge into a crash — the deletion has already been committed.
            return int(getattr(result, "rowcount", 0) or 0)


__all__ = ["SqlAlchemyOperatorAccessLog"]
