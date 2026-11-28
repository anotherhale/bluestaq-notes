"""Who read a platform-wide operational view, and when (R36).

``OPS_OPERATOR_USER_IDS`` decides *whether* a caller may read process-wide
totals. It says nothing about *who actually did*, so an operator allowlist could
not be audited from inside the system: the only trace of a privileged read was
whatever a log pipeline happened to retain.

This port is the record. Every granted read of an operational view appends one
entry naming the caller, the operation, and the moment. The moment is supplied
by the caller rather than read here, because the same
:class:`~notes_service.application.ports.clock.Clock` that stamped the request
must stamp the record — a record timed by a second clock is the defect R29
already cost this programme once.

The record is written *after* the gate allows and never before it, so a refused
caller leaves no trace. That ordering is deliberate: an entry in this log is
evidence that a caller was authorised, and a denial written into it would make
the log answer a question nobody asked while making "who read this" noisier.

Entries are counts of access, not of content: an operation name is a fixed
identifier chosen by the use case, never caller-supplied text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from notes_service.domain.identity.value_objects import UserId


@dataclass(frozen=True, slots=True)
class OperatorAccessEntry:
    """One granted read of an operational view.

    ``position`` is the adapter's monotonic insertion ordinal: a positive integer
    that strictly increases with every recorded entry, and ``None`` only when the
    adapter cannot name one. It exists so a reader can page *past* an entry it has
    already seen without re-counting, which a limit alone cannot do once the log
    grows while the reader is walking it.

    It is not a count of *entries in the log*: retention may delete old rows and a
    ring may drop them, so position 42 does not mean "forty-two entries exist". It
    only orders them. Treat it as opaque — compare, never arithmetic.
    """

    user_id: UserId
    operation: str
    at: datetime
    position: int | None = None


@runtime_checkable
class OperatorAccessLog(Protocol):
    """Append-only record of granted operational reads."""

    @property
    def name(self) -> str: ...

    def record(self, entry: OperatorAccessEntry) -> None:
        """Append one granted read. Called only after a gate allows."""
        ...

    def recent(
        self, *, limit: int, before_position: int | None = None
    ) -> tuple[OperatorAccessEntry, ...]:
        """Up to ``limit`` entries, newest first, strictly *older* than a cursor.

        ``before_position`` is a keyset cursor: when given, only entries whose
        :attr:`OperatorAccessEntry.position` is strictly less are returned, so the
        caller resumes exactly after the last entry it saw. ``None`` starts at the
        newest entry.

        Why a cursor and not an ``offset``
        ----------------------------------
        Reading this log **appends to it** — the gate records the granted read
        before the report reads the page. An ``OFFSET`` page would therefore shift
        under the reader by one row per page, so page *n+1* would repeat an entry
        page *n* already showed and skip another outright. A keyset cursor is
        anchored to an entry rather than to a count, so rows inserted at the head
        while the reader walks the log cannot move the window.

        A cursor is required for correctness, not only for convenience: with
        ``limit`` alone, any entry older than the newest ``limit`` is unreachable,
        and the access log is retained for up to a decade, so "read the trail" would
        silently mean "read its most recent 500 rows".

        Adapters that cannot name a position (``position is None`` on every entry)
        ignore the cursor and behave as a plain capped read; they cannot offer
        paging and must not pretend to.
        """
        ...

    def oldest_retained(self) -> datetime | None:
        """The stamp of the oldest entry still in the log, or ``None`` if empty.

        This exists because "the oldest survivor" cannot be derived from
        :meth:`recent`. ``recent`` answers newest-first and therefore has to be
        capped, so the last entry of a page is the oldest of *that page*, not of
        the log. A purge result built that way reports a ``retained_from`` that
        drifts forward as the log grows: with 1500 survivors the field claims the
        trail starts at entry 1000's timestamp, silently discarding the 500 oldest
        entries' worth of history from the one number an auditor reads to learn how
        far back the retained evidence reaches. The answer must be exact and must
        not depend on how much traffic arrived, so it is asked for directly rather
        than inferred from a bounded page.

        ``None`` means the log is empty. An empty log is a fact, not a failure,
        and a caller must be able to state it without inventing a timestamp.
        """
        ...

    def purge_before(self, *, cutoff: datetime) -> int:
        """Delete entries stamped strictly before ``cutoff``; return the count.

        The one mutation the append-only log permits, and it is deliberately the
        narrowest possible: it can only *remove the oldest* rows, it cannot
        rewrite a surviving one, and it takes an instant rather than a predicate
        so a caller cannot express "delete this user's entries" or any other
        selective erasure. ``cutoff`` comes from an
        :class:`~notes_service.domain.governance.retention.OperatorAccessRetentionPolicy`,
        which enforces a floor, so an adapter is never the thing deciding how long
        evidence is kept.

        An entry stamped exactly ``cutoff`` is *kept*: the policy's boundary is
        inclusive at its near edge, and the adapter must agree with it or the two
        would disagree at the one row that matters most — the oldest one.

        Adapters that cannot purge (a demo ring that already drops its oldest
        entry on write) return ``0`` rather than raising: nothing is being kept
        forever there, so there is nothing to retain.
        """
        ...


__all__ = ["OperatorAccessEntry", "OperatorAccessLog"]
