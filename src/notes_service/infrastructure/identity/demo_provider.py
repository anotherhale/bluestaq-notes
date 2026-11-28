"""Demo identity provider (development and testing only).

This provider exists to exercise multi-user behaviour without a login screen.
It is *not* production authentication. A caller selects a seeded demo user with
the ``X-Demo-User-ID`` header; the value is validated against the database and
an unknown or inactive user is rejected. The provider is disabled in production
by :mod:`notes_service.bootstrap.config`, and refuses to construct there too.

Security properties:

* the header never grants a user that is not already persisted and active;
* an invalid demo selection does not fall back to any other identity;
* the provider never mints identities, so guessing an arbitrary id fails;
* a selected user must still hold tenant access, so a suspended or deprovisioned
  membership cannot authenticate even in development. This is the same rule the
  OIDC login callback, session resolution and bearer provider apply — a demo
  header is an identity selection, not an entitlement.
"""

from __future__ import annotations

from notes_service.application.errors import AuthenticationError
from notes_service.application.ports.identity import AuthenticationContext
from notes_service.application.use_cases.tenant_access import ensure_tenant_access
from notes_service.domain.errors import ValidationError
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import UserId

DEMO_HEADER = "X-Demo-User-ID"


class DemoIdentityProvider:
    """Resolves a seeded demo user from the ``X-Demo-User-ID`` header."""

    def __init__(self, *, uow_factory: object, enabled: bool, environment_is_production: bool):
        self._uow_factory = uow_factory
        self._enabled = enabled
        self._environment_is_production = environment_is_production
        if enabled and environment_is_production:
            # Defence in depth: the settings validator already forbids this
            # combination, but the provider refuses to exist anyway.
            raise ValueError("DemoIdentityProvider must not be enabled in production")

    @property
    def name(self) -> str:
        return "demo"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def authenticate(self, context: AuthenticationContext) -> Principal:
        if not self._enabled:
            raise AuthenticationError(
                "demo authentication is disabled; supply a valid bearer token"
            )

        raw = context.demo_user_id_header
        if not raw:
            raise AuthenticationError(f"missing {DEMO_HEADER} header (development authentication)")

        try:
            user_id = UserId.parse(raw)
        except ValidationError as exc:
            raise AuthenticationError(f"invalid {DEMO_HEADER}: not a UUID") from exc

        with self._uow_factory() as uow:  # type: ignore[operator]
            user = uow.users.get(user_id)
            if user is None:
                raise AuthenticationError("unknown demo user")
            if not user.is_active:
                raise AuthenticationError("demo user is not active")
            # Selecting a demo identity is not an entitlement to a tenant. The
            # same rule guards the OIDC, session and bearer paths; without it a
            # revoked membership would keep working in development.
            ensure_tenant_access(uow, user_id=str(user.id))

            return Principal(
                user_id=user.id,
                email=user.email,
                display_name=user.display_name,
                provider=self.name,
                subject=str(user.id),
                claims={"demo": True},
            )
