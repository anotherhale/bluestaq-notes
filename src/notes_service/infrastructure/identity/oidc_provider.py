"""OIDC/JWT identity provider.

Validates a bearer JWT against a provider's JWKS and maps the ``sub`` claim to a
local user through the ``external_identities`` table. Cryptography is delegated
entirely to PyJWT (which uses ``cryptography``); no signature verification is
hand-rolled.

The provider is only constructed when ``auth_oidc_enabled`` is true, and it
fails fast if its configuration is incomplete. It never auto-provisions users:
an unrecognised subject is rejected, so a valid token for an unknown user cannot
create access. Provisioning would be a deliberate, separately-reviewed change.
"""

from __future__ import annotations

from typing import Any

import jwt
from jwt import PyJWKClient

from notes_service.application.errors import AuthenticationError
from notes_service.application.ports.identity import AuthenticationContext
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import ProviderSubject


class OidcIdentityProvider:
    """Validates RS256/ES256 JWTs issued by a configured OIDC provider."""

    def __init__(
        self,
        *,
        uow_factory: object,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: list[str],
        leeway_seconds: int = 30,
        provider_name: str = "oidc",
    ) -> None:
        if not issuer or not audience or not jwks_url:
            raise ValueError("OIDC provider requires issuer, audience, and jwks_url")
        if not algorithms:
            raise ValueError("OIDC provider requires at least one allowed algorithm")
        # Refuse obviously unsafe algorithms for token verification.
        unsafe = {"none", "HS256", "HS384", "HS512"}
        if unsafe & set(algorithms):
            raise ValueError(f"refusing unsafe JWT algorithms: {sorted(unsafe & set(algorithms))}")

        self._uow_factory = uow_factory
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._leeway = leeway_seconds
        self._provider_name = provider_name
        self._jwks_client = PyJWKClient(jwks_url, cache_keys=True)

    @property
    def name(self) -> str:
        return self._provider_name

    def authenticate(self, context: AuthenticationContext) -> Principal:
        token = self._extract_bearer(context.authorization_header)
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={
                    "require": ["exp", "iat", "sub", "iss", "aud"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("token has expired") from exc
        except jwt.InvalidTokenError as exc:
            # Covers bad signature, wrong issuer/audience, missing claims, etc.
            raise AuthenticationError("invalid bearer token") from exc

        subject = str(claims["sub"])
        with self._uow_factory() as uow:  # type: ignore[operator]
            identity = uow.external_identities.find_by_provider_subject(
                ProviderSubject(provider=self._provider_name, subject=subject)
            )
            if identity is None:
                raise AuthenticationError("no local user is linked to this identity")
            user = uow.users.get(identity.user_id)
            if user is None or not user.is_active:
                raise AuthenticationError("linked user is missing or inactive")

            return Principal(
                user_id=user.id,
                email=user.email,
                display_name=user.display_name,
                provider=self._provider_name,
                subject=subject,
                claims={"iss": claims.get("iss"), "aud": claims.get("aud")},
            )

    @staticmethod
    def _extract_bearer(header: str | None) -> str:
        if not header:
            raise AuthenticationError("missing Authorization header")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationError("Authorization header must use the Bearer scheme")
        return token.strip()
