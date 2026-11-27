"""The single gate in front of every platform-wide operational view (R36).

``GetWithholdingReport`` (NEX-037) and ``SweepAuditIntegrity`` (NEX-042) each
carried their own copy of the same four lines: compare the caller against the
allowlist, raise ``ResourceNotFound`` if absent, proceed. Two copies of a
security gate is already one too many — they can drift, and nothing noticed when
they did. They also shared the defect R36 records: a grant with no record of its
use.

This module is that gate, in one place, with the record attached:

* an empty allowlist admits **nobody**, not everybody — the default deployment
  exposes no operational view;
* a caller who is not listed gets ``ResourceNotFound``, so a probe cannot tell a
  forbidden endpoint from a path that does not exist;
* a granted read appends exactly one :class:`OperatorAccessEntry`, stamped by the
  caller's own clock.

The gate is deliberately **not** an
:class:`~notes_service.application.ports.authorization.AuthorizationProvider`
decision. That policy governs note content, and platform-wide operational data
must never become reachable as a side effect of content access.

What this does *not* solve: the allowlist is still a static environment variable
with no expiry. Rotation and per-operation scoping belong to the
scoped-capability work; this slice makes use of the grant *visible*, which is the
half that can be built correctly now without inventing a capability system
inside a security gate.
"""

from __future__ import annotations

from notes_service.application.errors import ResourceNotFound
from notes_service.application.ports.clock import Clock
from notes_service.application.ports.operator_access import (
    OperatorAccessEntry,
    OperatorAccessLog,
)
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import UserId


class OperatorGate:
    """Authorise an operator read and record that it happened."""

    def __init__(
        self,
        *,
        operator_ids: frozenset[UserId],
        access_log: OperatorAccessLog,
        clock: Clock,
    ) -> None:
        self._operator_ids = operator_ids
        self._access_log = access_log
        self._clock = clock

    @property
    def operator_ids(self) -> frozenset[UserId]:
        """The allowlist as configured. Read-only; empty means nobody."""
        return self._operator_ids

    def authorize(self, principal: Principal, *, operation: str) -> None:
        """Allow an operator read of ``operation``, or raise ``ResourceNotFound``.

        ``operation`` is a fixed identifier chosen by the use case (e.g.
        ``"withholding-report"``), never text a caller supplied, so the log
        cannot be written to with attacker-chosen strings.
        """
        # Refuse before recording: a denied caller must leave no trace, or the
        # log would answer "who was refused" instead of "who read this".
        if principal.user_id not in self._operator_ids:
            raise ResourceNotFound("not found")

        self._access_log.record(
            OperatorAccessEntry(
                user_id=principal.user_id,
                operation=operation,
                at=self._clock.now(),
            )
        )


__all__ = ["OperatorGate"]
