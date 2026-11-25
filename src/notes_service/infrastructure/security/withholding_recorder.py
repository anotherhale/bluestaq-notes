"""In-memory, process-local withholding recorder.

A *demo* adapter, in the same sense as
:class:`~notes_service.infrastructure.security.rate_limiter.InMemoryRateLimiter`:
counts live in the process, so they are per-worker and cleared by a restart. It
exists to prove the port and to make over-cap withholding visible in a log line
and a snapshot. A production deployment would swap in a shared store or an
exporter implementing the same
:class:`~notes_service.application.ports.withholding.WithholdingRecorder` port.

Counts only. Field *names* are counted, never values: a withheld value must stay
unreachable, so nothing here can reconstruct what was withheld.
"""

from __future__ import annotations

import threading

from notes_service.application.ports.withholding import WithholdingSnapshot


class InMemoryWithholdingRecorder:
    """Running totals of advisories and over-cap withholdings."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._advisories_total = 0
        self._over_cap_advisories_total = 0
        self._over_cap_fields_total = 0

    @property
    def name(self) -> str:
        return "in-memory"

    def record(self, *, over_cap_field_names: tuple[str, ...]) -> None:
        """Count one advisory, and the fields it had to withhold for size.

        An advisory with no over-cap fields still counts toward
        ``advisories_total``: a rate is only meaningful against a denominator of
        all advisories, not just the degraded ones.
        """
        with self._lock:
            self._advisories_total += 1
            if over_cap_field_names:
                self._over_cap_advisories_total += 1
                self._over_cap_fields_total += len(over_cap_field_names)

    def snapshot(self) -> WithholdingSnapshot:
        with self._lock:
            return WithholdingSnapshot(
                advisories_total=self._advisories_total,
                over_cap_advisories_total=self._over_cap_advisories_total,
                over_cap_fields_total=self._over_cap_fields_total,
            )
