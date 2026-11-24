"""OIDC login use cases.

These drive the browser-facing half of the authorization-code flow. They are
transport-neutral: they take and return plain data, and the presentation layer
turns that into redirects and cookies.

The security properties live here, not in the router:

* ``BeginLogin`` mints a fresh single-use transaction (``state``, ``nonce``,
  PKCE verifier) and persists it *before* the redirect, so a callback arriving
  with an unknown ``state`` is rejected rather than accepted.
* ``CompleteLogin`` consumes the transaction exactly once, lets the relying
  party verify the ID token against the transaction's ``nonce``, then requires
  the verified subject to map to an active local user. There is **no**
  auto-provisioning: an unknown or inactive subject is denied. A successful
  login establishes a session whose raw cookie secret is returned to the caller
  once and never persisted.
* Both ``CompleteLogin`` and ``ResolveSession`` additionally require the user to
  still hold tenant access, so a suspended or deprovisioned membership cannot
  authenticate and an existing session stops working on the next request.
* ``ResolveSession`` and ``EndSession`` cover the session lifecycle; resolution
  is fail-closed and slides the idle window.

Every failure is an :class:`AuthenticationError`, which the presentation layer
maps to ``401``. No branch falls back to another identity mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from notes_service.application.errors import AuthenticationError
from notes_service.application.ports.clock import Clock
from notes_service.application.ports.oidc import OidcRelyingParty
from notes_service.application.use_cases.base import UnitOfWorkFactory
from notes_service.application.use_cases.tenant_access import ensure_tenant_access
from notes_service.domain.errors import DomainError
from notes_service.domain.identity.entities import User
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.session import (
    LoginTransaction,
    UserSession,
    generate_session_token,
    hash_session_token,
)
from notes_service.domain.identity.value_objects import ProviderSubject

#: How long a session may sit idle before it must be re-established.
DEFAULT_SESSION_IDLE_TTL = timedelta(hours=8)

#: Absolute cap on a session's life regardless of activity.
DEFAULT_SESSION_ABSOLUTE_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """What the router needs to redirect the browser to the IdP."""

    authorization_url: str
    state: str


@dataclass(frozen=True, slots=True)
class EstablishedSession:
    """The result of a successful login.

    ``session_token`` is the raw cookie value. It is returned to the caller so it
    can be set on the response and is never stored or logged.
    """

    session_token: str
    session: UserSession
    principal: Principal


class OidcUseCase:
    """Shared plumbing for use cases that need the relying party."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        relying_party: OidcRelyingParty,
        session_idle_ttl: timedelta = DEFAULT_SESSION_IDLE_TTL,
        session_absolute_ttl: timedelta = DEFAULT_SESSION_ABSOLUTE_TTL,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._relying_party = relying_party
        self._session_idle_ttl = session_idle_ttl
        self._session_absolute_ttl = session_absolute_ttl

    def _principal_for(self, user: User, *, provider: str, subject: str) -> Principal:
        return Principal(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            provider=provider,
            subject=subject,
            claims={"mechanism": "session"},
        )


class BeginLogin(OidcUseCase):
    """Start a login: persist a transaction and build the authorization URL."""

    def run(self, *, login_hint: str | None = None) -> AuthorizationRequest:
        now = self._clock.now()
        transaction = LoginTransaction.begin(redirect_uri=self._relying_party.redirect_uri, now=now)
        with self._uow_factory() as uow:
            # Opportunistic cleanup keeps the table bounded without a cron job.
            uow.login_transactions.purge_expired(before=now)
            uow.login_transactions.add(transaction)
            uow.commit()

        url = self._relying_party.authorization_url(
            state=transaction.state,
            nonce=transaction.nonce,
            code_challenge=transaction.code_challenge,
            login_hint=login_hint,
        )
        return AuthorizationRequest(authorization_url=url, state=transaction.state)


class CompleteLogin(OidcUseCase):
    """Finish a login from the IdP callback."""

    def run(self, *, state: str, code: str) -> EstablishedSession:
        if not state or not code:
            raise AuthenticationError("missing authorization response parameters")

        now = self._clock.now()
        with self._uow_factory() as uow:
            transaction = uow.login_transactions.get_by_state(state)
            if transaction is None:
                # Unknown state: a forged callback, or one already cleaned up.
                raise AuthenticationError("unknown or expired login transaction")

            try:
                transaction.assert_usable(now)
            except DomainError as exc:
                raise AuthenticationError("login transaction is no longer valid") from exc

            # Consume before the exchange so a concurrent replay of the same
            # callback cannot race us into two sessions.
            transaction.consume(now)
            uow.login_transactions.save(transaction)
            uow.commit()

        id_token = self._relying_party.exchange_code(
            code=code,
            code_verifier=transaction.code_verifier,
            expected_nonce=transaction.nonce,
        )

        user = self._resolve_user(subject=id_token.subject)

        token = generate_session_token()
        session = UserSession.establish(
            user_id=user.id,
            provider=self._relying_party.provider_name,
            subject=id_token.subject,
            token=token,
            idle_ttl=self._session_idle_ttl,
            absolute_ttl=self._session_absolute_ttl,
            now=now,
        )
        with self._uow_factory() as uow:
            uow.sessions.add(session)
            uow.commit()

        principal = self._principal_for(
            user, provider=self._relying_party.provider_name, subject=id_token.subject
        )
        return EstablishedSession(session_token=token, session=session, principal=principal)

    def _resolve_user(self, *, subject: str) -> User:
        """Map a verified external subject to an active local user, else deny.

        The lookup key is ``(provider, subject)`` — never the email, which is not
        a stable identifier and can be reassigned at the IdP.
        """
        if not subject:
            raise AuthenticationError("identity token has no subject")
        provider = self._relying_party.provider_name
        with self._uow_factory() as uow:
            identity = uow.external_identities.find_by_provider_subject(
                ProviderSubject(provider=provider, subject=subject)
            )
            if identity is None:
                # No auto-provisioning: an unlinked subject is denied.
                raise AuthenticationError("no local account is linked to this identity")
            user = uow.users.get(identity.user_id)
            if user is None or not user.is_active:
                raise AuthenticationError("linked account is missing or inactive")
            # A verified identity is not by itself an entitlement: a suspended
            # or deprovisioned membership must not be able to start a session.
            ensure_tenant_access(uow, user_id=str(user.id))
            return user


class ResolveSession(OidcUseCase):
    """Resolve a cookie secret into a live principal, or raise."""

    def run(self, *, session_token: str) -> Principal:
        if not session_token:
            raise AuthenticationError("no session presented")
        now = self._clock.now()
        token_hash = hash_session_token(session_token)
        with self._uow_factory() as uow:
            session = uow.sessions.get_by_token_hash(token_hash)
            if session is None or not session.is_active(now):
                raise AuthenticationError("session is invalid or expired")
            user = uow.users.get(session.user_id)
            if user is None or not user.is_active:
                raise AuthenticationError("session owner is missing or inactive")
            # Re-checked on every request, not only at login: this is what makes
            # a revocation take effect immediately instead of at session expiry.
            ensure_tenant_access(uow, user_id=str(user.id))
            session.touch(now, idle_ttl=self._session_idle_ttl)
            uow.sessions.save(session)
            uow.commit()
        return self._principal_for(user, provider=session.provider, subject=session.subject)


class EndSession(OidcUseCase):
    """Revoke a session. Idempotent and silent on an unknown token."""

    def run(self, *, session_token: str) -> None:
        if not session_token:
            return
        now = self._clock.now()
        token_hash = hash_session_token(session_token)
        with self._uow_factory() as uow:
            session = uow.sessions.get_by_token_hash(token_hash)
            if session is None:
                return
            session.revoke(now)
            uow.sessions.save(session)
            uow.commit()


__all__ = [
    "DEFAULT_SESSION_ABSOLUTE_TTL",
    "DEFAULT_SESSION_IDLE_TTL",
    "AuthorizationRequest",
    "BeginLogin",
    "CompleteLogin",
    "EndSession",
    "EstablishedSession",
    "OidcUseCase",
    "ResolveSession",
]
