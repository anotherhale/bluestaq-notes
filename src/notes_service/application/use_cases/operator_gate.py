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

What this still does *not* solve: the allowlist is a static environment variable
with no expiry. Rotation belongs to the scoped-capability work. This slice makes
each grant narrow and each use visible, which is the part that can be built
correctly now without inventing a capability system inside a security gate.
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

WILDCARD = "*"
"""The one scope token that matches every operation.

Spelled out rather than left implicit: an operator who may read everything is a
deliberate, visible decision, not the consequence of an omitted field.
"""


class OperatorGate:
    """Authorise an operator read, within its scope, and record that it happened."""

    def __init__(
        self,
        *,
        operator_scopes: dict[UserId, frozenset[str]],
        access_log: OperatorAccessLog,
        clock: Clock,
    ) -> None:
        self._operator_scopes = {
            user_id: frozenset(scopes) for user_id, scopes in operator_scopes.items()
        }
        self._access_log = access_log
        self._clock = clock

    @property
    def operator_ids(self) -> frozenset[UserId]:
        """Every operator with at least one scope. Empty means nobody.

        An id mapped to an empty scope set is not an operator: it may read
        nothing, so reporting it as one would overstate the allowlist.
        """
        return frozenset(user_id for user_id, scopes in self._operator_scopes.items() if scopes)

    def scopes_for(self, user_id: UserId) -> frozenset[str]:
        """The operations ``user_id`` may use. Empty when not an operator at all."""
        return self._operator_scopes.get(user_id, frozenset())

    def authorize(self, principal: Principal, *, operation: str) -> None:
        """Allow an operator read of ``operation``, or raise ``ResourceNotFound``.

        ``operation`` is a fixed identifier chosen by the use case (e.g.
        ``"withholding-report"``), never text a caller supplied, so the log
        cannot be written to with attacker-chosen strings.

        A listed operator whose scope does not include ``operation`` is refused
        exactly like an unlisted caller. A ``403`` here would confirm both that
        the endpoint exists and that the caller is *some* kind of operator — two
        facts an ordinary user has no need for.
        """
        # Refuse before recording: a denied caller must leave no trace, or the
        # log would answer "who was refused" instead of "who read this".
        if not self._permits(principal.user_id, operation):
            raise ResourceNotFound("not found")

        self._access_log.record(
            OperatorAccessEntry(
                user_id=principal.user_id,
                operation=operation,
                at=self._clock.now(),
            )
        )

    def _permits(self, user_id: UserId, operation: str) -> bool:
        scopes = self._operator_scopes.get(user_id)
        if not scopes:
            return False
        return WILDCARD in scopes or operation in scopes


__all__ = ["WILDCARD", "OperatorGate"]
