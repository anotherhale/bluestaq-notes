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

    def active_for(self, user_id: UserId, *, now: datetime) -> frozenset[str]:
        """The scopes ``user_id`` may use at ``now``. Empty when none apply.

        Expired, not-yet-valid, and never-granted all yield the empty set: a
        caller cannot distinguish them, and none of them confers anything.
        """
        ...

    def active_grants(self, *, now: datetime) -> tuple[OperatorGrant, ...]:
        """Every grant that is active at ``now``, in insertion order.

        Insertion order, not ``(user, scope)`` order, so the answer is stable for
        a given store state and a test can assert on it without sorting.
        """
        ...


__all__ = ["OperatorGrantRepository"]
