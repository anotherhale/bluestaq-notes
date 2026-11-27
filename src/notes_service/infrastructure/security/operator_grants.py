"""In-memory, process-local operator grant store.

A *demo* adapter in the same sense as
:class:`~notes_service.infrastructure.security.operator_access_log.InMemoryOperatorAccessLog`:
grants live in the process, so they are per-worker and cleared by a restart. It
exists to prove the port and to make expiry and holder visibility real without
standing up storage. A production deployment would swap in a table-backed
adapter implementing the same
:class:`~notes_service.application.ports.operator_grants.OperatorGrantRepository`
port; neither the gate nor the use cases change.

Identity of a stored grant is ``(user_id, scope, granted_at)``. Two grants that
differ only in ``expires_at`` are the *same* grant re-issued, and the later
record wins — an operator extending a deadline should not have to remember which
of two rows is live. Two grants that differ in ``granted_at`` are distinct: a
revoke-and-reissue is a new fact about the same permission, and collapsing it
would erase the history of when the current window opened.
"""

from __future__ import annotations

import threading
from datetime import datetime

from notes_service.domain.identity.operator_grant import OperatorGrant
from notes_service.domain.identity.value_objects import UserId


class InMemoryOperatorGrantRepository:
    """Bounded-free, insertion-ordered store of operator grants."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Keyed by (user, scope, granted_at) so a re-issue replaces in place
        # while a genuinely new grant is appended. A dict preserves insertion
        # order, which is what ``active_grants`` promises.
        self._grants: dict[tuple[UserId, str, datetime], OperatorGrant] = {}

    @property
    def name(self) -> str:
        return "in-memory"

    def add(self, grant: OperatorGrant) -> None:
        if not isinstance(grant, OperatorGrant):
            raise TypeError("grant must be an OperatorGrant")
        key = (grant.user_id, grant.scope, grant.granted_at)
        with self._lock:
            self._grants[key] = grant

    def active_for(self, user_id: UserId, *, now: datetime) -> frozenset[str]:
        with self._lock:
            grants = tuple(self._grants.values())
        return frozenset(
            grant.scope for grant in grants if grant.user_id == user_id and grant.is_active(now=now)
        )

    def active_grants(self, *, now: datetime) -> tuple[OperatorGrant, ...]:
        with self._lock:
            grants = tuple(self._grants.values())
        return tuple(grant for grant in grants if grant.is_active(now=now))


__all__ = ["InMemoryOperatorGrantRepository"]
