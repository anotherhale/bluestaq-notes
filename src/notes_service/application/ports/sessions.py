"""Persistence ports for the browser-session and OIDC-login lifecycle.

These sit beside the identity ports: ``ExternalIdentityRepository`` links an
IdP subject to a local user, while these ports manage the *session* that results
from a successful login and the *transient transaction* that protects the
authorization-code exchange.

Neither port ever commits; the Unit of Work owns transaction boundaries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from notes_service.domain.identity.session import LoginTransaction, UserSession
from notes_service.domain.identity.value_objects import UserId


@runtime_checkable
class LoginTransactionRepository(Protocol):
    """Persistence port for in-flight OIDC login transactions."""

    def add(self, transaction: LoginTransaction) -> None: ...

    def get_by_state(self, state: str) -> LoginTransaction | None:
        """Look up by the ``state`` echoed back on the callback."""
        ...

    def save(self, transaction: LoginTransaction) -> None: ...

    def delete(self, transaction: LoginTransaction) -> None: ...

    def purge_expired(self, *, before: datetime) -> int:
        """Delete transactions that expired before ``before``.

        Returns the number of rows removed. Called opportunistically during the
        login flow so the table cannot grow without bound.
        """
        ...


@runtime_checkable
class UserSessionRepository(Protocol):
    """Persistence port for established user sessions.

    Lookups are by token *hash* only: the raw cookie secret never reaches the
    repository, so it cannot be logged or persisted by accident.
    """

    def add(self, session: UserSession) -> None: ...

    def get_by_token_hash(self, token_hash: str) -> UserSession | None: ...

    def save(self, session: UserSession) -> None: ...

    def list_active_for_user(self, user_id: UserId, *, now: datetime) -> list[UserSession]:
        """Sessions for a user that are neither revoked nor past either clock."""
        ...

    def revoke_all_for_user(self, user_id: UserId, *, now: datetime) -> int:
        """Revoke every live session for a user; returns the count revoked.

        Used when an account is suspended or a credential compromise is
        suspected, so revocation is immediate rather than waiting for expiry.
        """
        ...

    def revoke_for_identity(
        self, user_id: UserId, *, provider: str, subject: str, now: datetime
    ) -> int:
        """Revoke live sessions a user established through one IdP identity.

        Narrower than :meth:`revoke_all_for_user` on purpose: unlinking one
        identity must not sign the user out of sessions established another way
        (a different provider, or a first-factor login).
        """
        ...
