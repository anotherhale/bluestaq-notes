"""Composite identity provider that routes between OIDC and demo auth.

Routing rule (important for security):

* If an ``Authorization: Bearer`` header is present, the request is treated as
  an OIDC request. A failure there is final — the request is **never** retried
  against demo authentication. This prevents a caller from downgrading a
  rejected token into a demo identity.
* Only when no bearer token is present does the provider consult the demo
  provider (if enabled), keyed off ``X-Demo-User-ID``.
* If neither mechanism can authenticate the request, it is rejected.
"""

from __future__ import annotations

from notes_service.application.errors import AuthenticationError
from notes_service.application.ports.identity import AuthenticationContext, IdentityProvider
from notes_service.domain.identity.principal import Principal


class RoutingIdentityProvider:
    """Selects a concrete identity provider per request."""

    def __init__(
        self,
        *,
        oidc_provider: IdentityProvider | None,
        demo_provider: IdentityProvider | None,
        session_provider: IdentityProvider | None = None,
    ) -> None:
        if oidc_provider is None and demo_provider is None and session_provider is None:
            raise ValueError("at least one identity provider must be configured")
        self._oidc = oidc_provider
        self._demo = demo_provider
        self._session = session_provider

    @property
    def name(self) -> str:
        parts = [p.name for p in (self._oidc, self._session, self._demo) if p is not None]
        return "routing(" + ",".join(parts) + ")"

    def authenticate(self, context: AuthenticationContext) -> Principal:
        has_bearer = bool(context.authorization_header and context.authorization_header.strip())

        if has_bearer:
            if self._oidc is None:
                # A bearer token was supplied but no OIDC provider is enabled:
                # reject outright rather than silently falling back to demo.
                raise AuthenticationError("bearer authentication is not enabled")
            # Do not fall back to demo if the token is invalid.
            return self._oidc.authenticate(context)

        # A presented session cookie is authoritative: if it is invalid the
        # request is rejected, never downgraded to the demo header. Otherwise a
        # stale or forged cookie would silently become a demo identity.
        if self._session is not None and context.session_cookie:
            return self._session.authenticate(context)

        if self._demo is not None:
            return self._demo.authenticate(context)

        raise AuthenticationError("authentication required")
