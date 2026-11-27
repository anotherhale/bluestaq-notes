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

Scoping (NEX-044)
-----------------

An unscoped allowlist grants *every* operational view to *every* listed
operator. That is a privilege-escalation shape, not a convenience: the person
who should read advisory withholding totals to tune a threshold has no reason to
read the platform-wide audit-integrity sweep, and vice versa. A grant that
cannot be narrowed is a grant that must be handed out whole.

A scope therefore names the operations an operator may use, and **a scope that
is not named is not granted**. ``"*"`` is the only way to express "all current
and future operations"; it is explicit and greppable, so a deployment that
granted it can be found by reading configuration rather than by inferring it
from an operator who happens to pass every check. Nothing grants ``"*"`` by
default.

Stored grants (NEX-045)
-----------------------

Scoping narrowed each grant but left two holes, both structural: a grant could
not **expire**, and nobody could **see who holds what**. Both need a record, so
the gate now consults an
:class:`~notes_service.application.ports.operator_grants.OperatorGrantRepository`
of :class:`~notes_service.domain.identity.operator_grant.OperatorGrant` values
rather than a static map. ``OPS_OPERATOR_USER_IDS`` is no longer the allowlist
itself; it is the *seed path* that populates the repository at startup.

The expiry check is the gate's own, evaluated against the gate's clock on every
call, so a grant that lapses mid-process stops working without a redeploy — and,
because the same ``Clock`` stamps both the check and the access-log entry, the
two cannot disagree about "now" (R29).
"""

from __future__ import annotations

from datetime import datetime

from notes_service.application.errors import ResourceNotFound
from notes_service.application.ports.clock import Clock
from notes_service.application.ports.operator_access import (
    OperatorAccessEntry,
    OperatorAccessLog,
)
from notes_service.application.ports.operator_grants import OperatorGrantRepository
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import UserId

WILDCARD = "*"
"""The one scope token that matches every operation.

Spelled out rather than left implicit: an operator who may read everything is a
deliberate, visible decision, not the consequence of an omitted field.
"""


class OperatorGate:
    """Authorise an operator read, within its scope and lifetime, and record it."""

    def __init__(
        self,
        *,
        grants: OperatorGrantRepository,
        access_log: OperatorAccessLog,
        clock: Clock,
    ) -> None:
        self._grants = grants
        self._access_log = access_log
        self._clock = clock

    @property
    def operator_ids(self) -> frozenset[UserId]:
        """Every operator with at least one *active* scope. Empty means nobody.

        Evaluated at the gate's clock, so an expired grant does not appear here.
        An id whose grants have all lapsed is not an operator: it may read
        nothing, so reporting it as one would overstate the allowlist — the one
        number an operator audit is supposed to be able to trust.
        """
        now = self._clock.now()
        return frozenset(grant.user_id for grant in self._grants.active_grants(now=now))

    def scopes_for(self, user_id: UserId) -> frozenset[str]:
        """The operations ``user_id`` may use right now. Empty when none apply."""
        return self._grants.active_for(user_id, now=self._clock.now())

    def authorize(self, principal: Principal, *, operation: str) -> None:
        """Allow an operator read of ``operation``, or raise ``ResourceNotFound``.

        ``operation`` is a fixed identifier chosen by the use case (e.g.
        ``"withholding-report"``), never text a caller supplied, so the log
        cannot be written to with attacker-chosen strings.

        A listed operator whose scope does not include ``operation`` — or whose
        grant has lapsed — is refused exactly like an unlisted caller. A ``403``
        here would confirm both that the endpoint exists and that the caller is
        *some* kind of operator: two facts an ordinary user has no need for.
        """
        now = self._clock.now()
        # Refuse before recording: a denied caller must leave no trace, or the
        # log would answer "who was refused" instead of "who read this".
        if not self._permits(principal.user_id, operation, now=now):
            raise ResourceNotFound("not found")

        self._access_log.record(
            OperatorAccessEntry(
                user_id=principal.user_id,
                operation=operation,
                at=now,
            )
        )

    def _permits(self, user_id: UserId, operation: str, *, now: datetime) -> bool:
        scopes = self._grants.active_for(user_id, now=now)
        if not scopes:
            return False
        return WILDCARD in scopes or operation in scopes


__all__ = ["WILDCARD", "OperatorGate"]
