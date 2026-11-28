"""Read back who has used an operator grant (R36).

NEX-2026-11-27-47 gave the operator allowlist a record: every *granted*
platform-wide read appends one
:class:`~notes_service.application.ports.operator_access.OperatorAccessEntry`.
It did not give anyone a way to read those entries, so the log was write-only
and R36's actual question — "who read the platform-wide totals" — was still
unanswerable from inside the system. A record nothing can read is not an audit
trail; it is a buffer with a comment.

This is the read side. It answers two questions an operator can act on:

* **has the grant been used at all** — ``entries`` empty over a long window means
  either nobody is an operator or the allowlist is stale, and both are worth
  knowing;
* **by whom, and for what** — each entry names the caller, the operation and the
  moment it was granted.

It is gated by the same
:class:`~notes_service.application.use_cases.operator_gate.OperatorGate` as every
other operational view, and fails closed the same way: an empty allowlist admits
nobody, and a non-operator gets ``404`` rather than ``403``. A granted read of
*this* view is itself recorded, so reading the access log is auditable too — an
operator cannot silently inspect the trail without adding to it.

The output carries no note content, no request payload and no caller-supplied
text. An operation name is a fixed identifier each use case chooses, never
something a request can set, so the log cannot be written to with attacker-chosen
strings (see :mod:`...ports.operator_access`).

Residual, stated rather than hidden: the current adapter is process-local and
bounded, so this reports recent activity on *this* worker, not a durable
deployment-wide history. Swapping the adapter for an append-only store changes
neither this use case nor its port. The response says which adapter answered, so
a reader cannot mistake a bounded in-memory view for a complete one.

It takes no unit of work: reading the log mutates nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from notes_service.application.errors import InvalidInput
from notes_service.application.ports.operator_access import (
    OperatorAccessEntry,
    OperatorAccessLog,
)
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.domain.identity.principal import Principal

OPERATION = "operator-access-report"

# A cap on how many entries one read may return. The underlying ring is already
# bounded, but the request chooses the limit, so it needs its own ceiling: an
# operator asking for a million entries should get the cap, not an allocation.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


@dataclass(frozen=True, slots=True)
class OperatorAccessSnapshot:
    """A window of granted operational reads, newest first.

    ``log_name`` and ``capacity`` describe the adapter that answered. A reader
    must be able to tell a bounded, process-local view from a durable one; an
    unlabelled list of ten entries reads like "ten reads ever happened" when it
    may mean "ten happened on this worker since the last restart".

    ``next_position`` is the keyset cursor for the next page — the ``position`` of
    the oldest entry in this window — and is ``None`` when this window is the last
    one. Follow it while it is not ``None`` to read the whole retained trail.
    """

    entries: tuple[OperatorAccessEntry, ...]
    limit: int
    log_name: str
    capacity: int
    next_position: int | None = None
    requested_from: int | None = None

    @property
    def truncated(self) -> bool:
        """True when this window is a *partial* read of the retained trail.

        A window is partial in either direction, and both are reported:

        * it **filled** (``next_position`` is set), so older entries exist past it;
        * it **started from a cursor** (``requested_from`` is set), so entries
          newer than the first one shown are not in it either.

        Reporting only the first would let a middle page — under-full, but nowhere
        near the start — read as "the whole trail". An audit view must over-report
        a possible gap rather than imply a completeness it cannot prove.

        An empty window is never truncated: with no entries it cannot name an
        anchor, and there is nothing in any direction to reach.
        """
        if not self.entries:
            return False
        return self.next_position is not None or self.requested_from is not None


class GetOperatorAccessReport:
    """Return recent granted operational reads to an authorized operator."""

    def __init__(
        self,
        *,
        access_log: OperatorAccessLog,
        operator_gate: OperatorGate,
    ) -> None:
        self._access_log = access_log
        self._operator_gate = operator_gate

    def run(
        self,
        principal: Principal,
        *,
        limit: int = DEFAULT_LIMIT,
        before_position: int | None = None,
    ) -> OperatorAccessSnapshot:
        # Gate first, read second: a refused caller must not learn how many
        # entries exist, and the gate records the grant before we touch the log.
        #
        # That record-before-read ordering is also why paging here is keyset, not
        # offset: this call appends an entry at the head, so an OFFSET page would
        # shift by one row per page and repeat an entry while skipping another.
        # The cursor is taken from the last entry of the *previous* page, so rows
        # arriving at the head while a reader walks the log cannot move its window.
        self._operator_gate.authorize(principal, operation=OPERATION)

        effective_limit = _clamp_limit(limit)
        start = _validate_cursor(before_position)
        entries = self._access_log.recent(limit=effective_limit, before_position=start)
        return OperatorAccessSnapshot(
            entries=entries,
            limit=effective_limit,
            log_name=self._access_log.name,
            capacity=_capacity_of(self._access_log),
            next_position=_next_position(entries, effective_limit),
            requested_from=start,
        )


def _next_position(entries: tuple[OperatorAccessEntry, ...], limit: int) -> int | None:
    """The cursor for the page after ``entries``, or ``None`` when there is none.

    Two conditions must both hold, and each rules out a different way of lying:

    * the window **filled** — an under-full page is the last page, so there is
      nothing more to fetch and offering a cursor would send the reader on a walk
      that never ends;
    * the oldest entry **names a position** — an adapter that cannot order its
      entries cannot continue from one, and inventing a cursor there would page
      over a set it cannot actually address.

    The cursor is the oldest entry's own position, not ``position - 1``: the port
    promises entries *strictly older* than the cursor, which excludes the entry
    already shown without assuming anything about how dense the positions are
    (retention and ring eviction both leave gaps in them).
    """
    if len(entries) < limit:
        return None
    oldest = entries[-1].position
    return oldest if oldest is not None else None


def _validate_cursor(before_position: int | None) -> int | None:
    """Reject a cursor that cannot address an entry.

    A non-positive cursor is a caller mistake, not a request for "the end": it
    would silently return the whole log, which is the opposite of what a cursor is
    for. Refusing it keeps ``next_position`` trustworthy — the next page is
    always a real continuation, never a wrap-around.
    """
    if before_position is None:
        return None
    if before_position < 1:
        raise InvalidInput("before_position must be a positive entry position")
    return before_position


def _clamp_limit(limit: int) -> int:
    """Constrain a caller-supplied limit into ``[1, MAX_LIMIT]``.

    A non-positive limit is a caller mistake, not a request for "everything":
    the port already answers ``()`` for it, but turning it into ``1`` here keeps
    the snapshot's ``truncated`` arithmetic honest.
    """
    if limit < 1:
        return 1
    return min(limit, MAX_LIMIT)


def _capacity_of(access_log: OperatorAccessLog) -> int:
    """The adapter's ring size, or ``0`` when it does not advertise one.

    The port does not require ``capacity`` — a durable store has no fixed ring.
    ``0`` therefore means "not a bounded ring", not "empty".
    """
    capacity = getattr(access_log, "capacity", 0)
    return capacity if isinstance(capacity, int) else 0


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "GetOperatorAccessReport",
    "OperatorAccessSnapshot",
]
