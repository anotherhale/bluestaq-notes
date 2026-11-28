"""Purge operator access-log entries that have aged out of the retention window (NEX-075).

NEX-072 made the record of a privileged read durable and said what it would not
do: nothing prunes the table. That was the right trade at the time — durability
first — but an unbounded audit table is a slow failure. It grows without limit,
``recent`` slows as it fills, and the record eventually costs more to keep than
the reads it describes. The fix is not an arbitrary ``DELETE``; it is a stated
window, enforced by a domain value, applied by an operator whose action is itself
recorded.

Why a use case and not a cron job with a ``DELETE``
---------------------------------------------------

* **Authority.** Pruning the audit trail is the most dangerous write in the
  subsystem: it removes the evidence the trail exists to hold. It is gated on its
  own scope, ``access-log-retention``, deliberately distinct from
  ``operator-access-report``. Reading who read what and being allowed to *erase*
  who read what are not the same authority, and a deployment that wants only a
  few people able to purge can say so by granting the read scope more widely. The
  same reasoning that separates ``operator-grant-issue`` from the read scope in
  NEX-074 applies here, with more force: erasing history is a graver act than
  adding to it.
* **A window, not a guess.** The cutoff comes from an
  :class:`~notes_service.domain.governance.retention.OperatorAccessRetentionPolicy`,
  which enforces a floor. No request can pass its own cutoff, so an operator
  cannot express "delete everything" or "delete this user's rows" — only "delete
  what the policy says has aged out".
* **The purge is itself recorded.** The gate writes an access entry for the purge
  *before* the delete runs. That entry is stamped ``now`` and is therefore inside
  the window, so the purge cannot delete the record of itself: the act of pruning
  the trail leaves a mark on the trail it just pruned. An audit trail that erased
  its own erasures would be worse than none.
* **Fail closed.** A non-operator gets ``404`` from the gate, exactly like every
  other operational view, so the endpoint's existence is not disclosed to a
  probing user.

It takes no unit of work: the log owns its own session (see the adapter), and a
purge must land independently of whatever content transaction the caller is in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from notes_service.application.ports.clock import Clock
from notes_service.application.ports.operator_access import OperatorAccessLog
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.domain.governance.retention import OperatorAccessRetentionPolicy
from notes_service.domain.identity.principal import Principal

OPERATION = "access-log-retention"
"""The scope that gates purging the operator access log.

Distinct from ``operator-access-report`` on purpose. An auditor who may read who
touched a platform-wide view should not, by that fact alone, be able to delete
the record. Erasing evidence and reading it are different authorities, and the
narrower one is the dangerous one.
"""


@dataclass(frozen=True, slots=True)
class OperatorAccessPurgeResult:
    """What one purge did, stated precisely enough to be audited.

    ``cutoff`` is the instant the policy produced; ``removed`` is how many rows the
    adapter deleted. ``retained_from`` is the oldest entry still in the log after
    the purge (``None`` when the log is now empty) — the reader can see both the
    boundary that was applied and what actually survived it, rather than trusting
    a count alone.
    """

    cutoff: datetime
    removed: int
    window_days: int
    retained_from: datetime | None
    log_name: str


class PurgeOperatorAccessLog:
    """Delete access-log entries older than the retention window (NEX-075)."""

    def __init__(
        self,
        *,
        access_log: OperatorAccessLog,
        operator_gate: OperatorGate,
        clock: Clock,
        policy: OperatorAccessRetentionPolicy,
    ) -> None:
        self._access_log = access_log
        self._operator_gate = operator_gate
        self._clock = clock
        self._policy = policy

    def execute(self, principal: Principal) -> OperatorAccessPurgeResult:
        # Gate first, and the gate records this purge as an access entry stamped
        # now — inside the window, so the purge cannot erase its own trace. A
        # refused caller reaches no store and leaves nothing to delete.
        self._operator_gate.authorize(principal, operation=OPERATION)

        now = self._clock.now()
        cutoff = self._policy.cutoff(now=now)
        removed = self._access_log.purge_before(cutoff=cutoff)

        oldest = self._access_log.oldest_retained()
        return OperatorAccessPurgeResult(
            cutoff=cutoff,
            removed=removed,
            window_days=self._policy.window.days,
            retained_from=oldest,
            log_name=self._access_log.name,
        )


__all__ = ["OPERATION", "OperatorAccessPurgeResult", "PurgeOperatorAccessLog"]
