"""The store of operator grants, as a port (NEX-045).

The gate must be able to ask two questions without knowing where grants live:

* **may this caller read this operation, right now** — one lookup per request;
* **who currently holds a grant, and until when** — an enumeration an operator
  audit needs and a static config string could never answer.

Those two questions have different shapes, and that is why this is a port rather
than a dict passed around: a demo adapter keeps them in a process, and a durable
adapter would keep them in a table, and neither changes the gate. The gate
depends on the behaviour, not the storage.

Reads are time-bounded by the caller
------------------------------------

``active_for`` and ``active_grants`` take ``now`` rather than reading a clock.
The domain value decides whether a grant is active (see
:meth:`~notes_service.domain.identity.operator_grant.OperatorGrant.is_active`);
this port only filters by that decision. Keeping the clock out of the adapter is
what makes R29's class of defect structurally impossible here: there is no second
clock to disagree with the request's own.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol, runtime_checkable

from notes_service.domain.identity.operator_grant import OperatorGrant
from notes_service.domain.identity.value_objects import UserId


@runtime_checkable
class OperatorGrantRepository(Protocol):
    """Storage for operator grants."""

    @property
    def name(self) -> str: ...

    def add(self, grant: OperatorGrant) -> None:
        """Store one grant. Idempotent for an identical grant.

        Adding the same ``(user, scope, granted_at)`` twice must not create two
        records — a seed that runs on every boot would otherwise accumulate a
        grant per restart, and an operator count would grow without anyone being
        granted anything new.
        """
        ...

    def replace_configured(self, grants: Iterable[OperatorGrant]) -> None:
        """Replace every grant the deployment configuration issued (NEX-046).

        ``OPS_OPERATOR_USER_IDS`` is a seed path, not an allowlist (NEX-045). With
        a process-local store, re-seeding on boot was harmless because the store
        was empty; with a durable one it is not, because a seed stamping
        ``granted_at`` from the boot clock is a *new* grant on every restart and
        the operator count would grow by the whole configuration each time.

        So the seed path reconciles rather than appends: grants that came from
        configuration (``granted_by is None``) are replaced with ``grants``, and
        grants a human issued are left exactly as they are. Configuration owns
        what configuration issued, and nothing else — a bounded incident grant
        must not be erased by a redeploy.
        """
        ...

    def active_for(self, user_id: UserId, *, now: datetime) -> frozenset[str]:
        """The scopes ``user_id`` may use at ``now``. Empty when none apply.

        Expired, not-yet-valid, revoked, and never-granted all yield the empty
        set: a caller cannot distinguish them, and none of them confers anything.
        """
        ...

    def revoke(
        self,
        user_id: UserId,
        scope: str,
        *,
        at: datetime,
        by: UserId,
    ) -> int:
        """Revoke every live grant of ``scope`` held by ``user_id`` (NEX-079).

        Returns the number of rows changed. A revocation is stamped as a fact on
        each matching grant (``revoked_at``/``revoked_by``) rather than deleting
        it, so the fact that authority was taken away — and by whom, and when —
        survives for an auditor. A grant already revoked is left untouched, so the
        *first* revocation is the one recorded and a second call reports zero
        rather than rewriting history.

        Only *currently live* grants are revoked — one that already lapsed
        confers nothing, so recording a revocation on it would be a misleading
        audit entry. ``at`` is the revoker's clock, matching every other bound in
        this subsystem (R29): the same clock that decides "live" stamps the
        revocation.

        This is deliberately keyed by ``(user, scope)`` and not by a single row:
        a holder may have several live grants of one scope only through a
        re-issue, and the operator asking to remove a permission means the
        permission, not one row. Revoking all live rows of that scope is the
        behaviour that matches the request.

        There is no self-revoke guard or attenuation check in the port: those are
        policy decisions for the use case, exactly as ``add`` leaves the issue
        policy to ``IssueOperatorGrant``.
        """
        ...

    def active_grants(self, *, now: datetime) -> tuple[OperatorGrant, ...]:
        """Every grant that is active at ``now``, in insertion order.

        Insertion order, not ``(user, scope)`` order, so the answer is stable for
        a given store state and a test can assert on it without sorting.
        """
        ...

    def revoked_grants(self) -> tuple[OperatorGrant, ...]:
        """Every grant that ended by revocation, in insertion order (NEX-080).

        The counterpart of ``revoke``. A revocation is deliberately *not* an
        absence — it is a recorded fact with an actor and an instant — but until
        this method existed nothing could read those facts back: they were written
        to ``revoked_at``/``revoked_by`` and were visible nowhere, so "who removed
        whose authority, and when" was answerable only by querying the database by
        hand. That is the same gap NEX-076 closed for ``rationale``.

        Requires no ``now``, unlike the active views: a revocation is historical
        and does not change with the clock. A row whose actor was later deleted
        still appears, with ``revoked_by`` ``None`` — the revocation outlives its
        revoker, which is the point of recording it.

        "Ended by revocation" is ``revoked_at IS NOT NULL``, not "revoked and not
        expired": a grant revoked while still live and a grant that lapsed on its
        own are different facts, and the second is not in this list at all.
        """
        ...


__all__ = ["OperatorGrantRepository"]
