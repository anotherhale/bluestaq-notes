"""In-memory, process-local operator access log.

A *demo* adapter, in the same sense as
:class:`~notes_service.infrastructure.security.withholding_recorder.InMemoryWithholdingRecorder`:
entries live in the process, so they are per-worker and cleared by a restart. It
exists to prove the port and to make a privileged read visible without standing
up storage. A production deployment would swap in an append-only store or an
exporter implementing the same
:class:`~notes_service.application.ports.operator_access.OperatorAccessLog` port.

The ring is bounded so a long-lived process cannot grow without limit. Dropping
the oldest entry is the honest trade: the log answers "who read this recently",
and it says so rather than pretending to be complete.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime

from notes_service.application.ports.operator_access import OperatorAccessEntry

DEFAULT_CAPACITY = 1000


class InMemoryOperatorAccessLog:
    """Bounded, newest-first record of granted operational reads."""

    def __init__(self, *, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._lock = threading.Lock()
        self._entries: deque[OperatorAccessEntry] = deque(maxlen=capacity)
        self._capacity = capacity

    @property
    def name(self) -> str:
        return "in-memory"

    @property
    def capacity(self) -> int:
        return self._capacity

    def record(self, entry: OperatorAccessEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def recent(self, *, limit: int) -> tuple[OperatorAccessEntry, ...]:
        if limit < 1:
            return ()
        with self._lock:
            newest_first = reversed(self._entries)
            return tuple(list(newest_first)[:limit])

    def purge_before(self, *, cutoff: datetime) -> int:
        """Nothing to purge: the ring already drops its oldest entry on write.

        This adapter keeps no unbounded history, so it never accumulates the debt
        a retention window exists to clear. Returning ``0`` rather than raising
        keeps the port's contract honest — a caller asking "did the purge remove
        anything" gets a truthful zero instead of an error it would have to special
        case. The durable adapter is where the answer is ever non-zero.
        """
        return 0


__all__ = ["DEFAULT_CAPACITY", "InMemoryOperatorAccessLog"]
