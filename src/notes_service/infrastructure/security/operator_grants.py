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
from collections.abc import Iterable
from dataclasses import replace
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

    def replace_configured(self, grants: Iterable[OperatorGrant]) -> None:
        with self._lock:
            # Revoked configuration grants are kept: a revocation is a standing
            # decision (NEX-079), not a value the seed may quietly reinstate on the
            # next boot. Without this, revoking a configuration grant would be
            # undone by every restart, which is exactly the fail-open the feature
            # exists to close.
            revoked_config: dict[tuple[UserId, str], OperatorGrant] = {
                (grant.user_id, grant.scope): grant
                for grant in self._grants.values()
                if grant.granted_by is None and grant.is_revoked
            }
            self._grants = {
                key: grant for key, grant in self._grants.items() if grant.granted_by is not None
            }
            # Re-instate the revoked configuration grants that were filtered out
            # above, so their revocation survives this reconciliation.
            for grant in revoked_config.values():
                self._grants[(grant.user_id, grant.scope, grant.granted_at)] = grant
            for grant in grants:
                if grant.granted_by is not None:
                    raise ValueError("replace_configured only accepts configuration grants")
                if (grant.user_id, grant.scope) in revoked_config:
                    continue
                self._grants[(grant.user_id, grant.scope, grant.granted_at)] = grant

    def active_for(self, user_id: UserId, *, now: datetime) -> frozenset[str]:
        with self._lock:
            grants = tuple(self._grants.values())
        return frozenset(
            grant.scope for grant in grants if grant.user_id == user_id and grant.is_active(now=now)
        )

    def revoke(self, user_id: UserId, scope: str, *, at: datetime, by: UserId) -> int:
        """Stamp a revocation on every live grant of ``scope`` (NEX-079).

        The in-memory store keeps grants keyed by ``(user, scope, granted_at)``, so
        one live grant of a scope may coexist with an older, already-lapsed one.
        Only the live ones are stamped — see the port docstring. Because
        ``granted_at`` is part of the key, the revoked grant is replaced under the
        same key: its identity in this store is the grant, and its revocation is a
        new fact about that grant, not a new grant.
        """
        with self._lock:
            changed = 0
            for key, grant in tuple(self._grants.items()):
                if grant.user_id != user_id or grant.scope != scope:
                    continue
                if grant.is_revoked or not grant.is_active(now=at):
                    continue
                self._grants[key] = replace(grant, revoked_at=at, revoked_by=by)
                changed += 1
            return changed

    def active_grants(self, *, now: datetime) -> tuple[OperatorGrant, ...]:
        with self._lock:
            grants = tuple(self._grants.values())
        return tuple(grant for grant in grants if grant.is_active(now=now))


__all__ = ["InMemoryOperatorGrantRepository"]
