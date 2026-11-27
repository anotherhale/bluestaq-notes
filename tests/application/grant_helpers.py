"""Shared construction of an operator grant store for tests (NEX-045).

The gate now reads a store rather than a dict, so every test that used to write
``operator_scopes={...}`` needs a populated repository instead. Building it in one
place keeps those tests about *what they assert* rather than about how a grant is
constructed, and means a change to the seed shape does not touch six files.

Grants made here are unbounded: these tests are about scope, not lifetime.
Lifetime has its own tests, which build grants with explicit bounds.
"""

from __future__ import annotations

from datetime import UTC, datetime

from notes_service.domain.identity.operator_grant import OperatorGrant
from notes_service.domain.identity.value_objects import UserId
from notes_service.infrastructure.security.operator_grants import (
    InMemoryOperatorGrantRepository,
)

GRANTED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def grants_from_scopes(
    scopes: dict[UserId, frozenset[str]],
    *,
    granted_at: datetime = GRANTED_AT,
) -> InMemoryOperatorGrantRepository:
    """A store holding one unbounded grant per (user, scope) pair."""
    repository = InMemoryOperatorGrantRepository()
    for user_id, held in scopes.items():
        for scope in sorted(held):
            repository.add(OperatorGrant(user_id=user_id, scope=scope, granted_at=granted_at))
    return repository


__all__ = ["GRANTED_AT", "grants_from_scopes"]
