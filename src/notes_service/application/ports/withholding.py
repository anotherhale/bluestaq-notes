"""A port that counts context withholdings across calls.

:class:`~notes_service.domain.agent.context.ContextEnvelope` already reports
*why* a field was withheld, per call. That makes one thin advisory visible to a
caller who looks, but it says nothing about frequency: a note title that is
routinely over the field cap degrades every advisory quietly, and no single
response is wrong enough to notice.

This port is that missing aggregate. It is deliberately *not* a metrics
dependency: the port is three methods, and the only adapter is process-local.
A shared store (a statsd client, a Prometheus registry, an OTLP exporter) can be
substituted later without touching a use case, exactly as with
:class:`~notes_service.application.ports.rate_limit.RateLimiter`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class WithholdingSnapshot:
    """Totals observed since process start.

    ``over_cap_total`` is the number worth alerting on: an above-ceiling
    exclusion is the classification policy working, whereas an over-cap value
    means an advisory was computed on less than the caller held.
    """

    advisories_total: int
    over_cap_advisories_total: int
    over_cap_fields_total: int

    @property
    def over_cap_rate(self) -> float:
        """Fraction of advisories degraded by an over-cap value, in ``[0.0, 1.0]``."""
        if self.advisories_total == 0:
            return 0.0
        return self.over_cap_advisories_total / self.advisories_total


@runtime_checkable
class WithholdingRecorder(Protocol):
    """Records one advisory's withheld field names and exposes running totals."""

    @property
    def name(self) -> str: ...

    def record(self, *, over_cap_field_names: tuple[str, ...]) -> None: ...

    def snapshot(self) -> WithholdingSnapshot: ...
