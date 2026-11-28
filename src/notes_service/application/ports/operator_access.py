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
    """One granted read of an operational view."""

    user_id: UserId
    operation: str
    at: datetime


@runtime_checkable
class OperatorAccessLog(Protocol):
    """Append-only record of granted operational reads."""

    @property
    def name(self) -> str: ...

    def record(self, entry: OperatorAccessEntry) -> None:
        """Append one granted read. Called only after a gate allows."""
        ...

    def recent(self, *, limit: int) -> tuple[OperatorAccessEntry, ...]:
        """The most recent entries, newest first, capped at ``limit``."""
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
