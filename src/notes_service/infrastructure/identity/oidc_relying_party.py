"""HTTP adapter for the OIDC relying-party port.

This is the only module that speaks OIDC over the wire. It performs the
authorization-code + PKCE exchange and verifies the ID token fully:

* signature, via the provider's JWKS (PyJWT + ``cryptography``; nothing is
  hand-rolled),
* ``iss`` and ``aud`` against configured values,
* ``exp``/``iat`` with a small leeway, and
* the ``nonce`` minted for this specific login transaction.

Any deviation raises :class:`AuthenticationError`. There is deliberately no
"best effort" path: a partially validated token is never returned.

The code exchange is also where a malicious or misconfigured IdP could try to
downgrade us, so the token endpoint's response is treated as untrusted input
until the ID token has been verified.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from notes_service.application.errors import AuthenticationError
from notes_service.application.ports.oidc import VerifiedIdToken

#: Bound on any single IdP HTTP call. A hanging IdP must not hang the request.
_HTTP_TIMEOUT_SECONDS = 10.0


class HttpOidcRelyingParty:
    """Authorization-code + PKCE relying party over HTTPS."""

    def __init__(
        self,
        *,
        provider_name: str,
        issuer: str,
        client_id: str,
        client_secret: str | None,
        authorization_endpoint: str,
        token_endpoint: str,
        jwks_url: str,
        redirect_uri: str,
        scopes: tuple[str, ...] = ("openid", "profile", "email"),
        algorithms: tuple[str, ...] = ("RS256",),
        leeway_seconds: int = 30,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not provider_name or not issuer or not client_id:
            raise ValueError("OIDC relying party requires provider_name, issuer, and client_id")
        if not authorization_endpoint or not token_endpoint or not jwks_url:
            raise ValueError("OIDC relying party requires all endpoint URLs")
        if not redirect_uri:
            raise ValueError("OIDC relying party requires a redirect_uri")
        unsafe = {"none", "HS256", "HS384", "HS512"}
        if not algorithms or unsafe & set(algorithms):
            raise ValueError(f"refusing unsafe or empty JWT algorithms: {sorted(unsafe)}")

        self._provider_name = provider_name
        self._issuer = issuer
        self._client_id = client_id
        self._client_secret = client_secret
        self._authorization_endpoint = authorization_endpoint
        self._token_endpoint = token_endpoint
        self._redirect_uri = redirect_uri
        self._scopes = scopes
        self._algorithms = list(algorithms)
        self._leeway = leeway_seconds
        self._http = http_client or httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS)
        self._jwks_client = PyJWKClient(jwks_url, cache_keys=True)

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, login_hint: str | None = None
    ) -> str:
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": " ".join(self._scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if login_hint:
            params["login_hint"] = login_hint
        separator = "&" if "?" in self._authorization_endpoint else "?"
        return f"{self._authorization_endpoint}{separator}{urlencode(params)}"

    def exchange_code(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> VerifiedIdToken:
        payload: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "client_id": self._client_id,
            "code_verifier": code_verifier,
        }
        if self._client_secret:
            payload["client_secret"] = self._client_secret

        try:
            response = self._http.post(self._token_endpoint, data=payload)
        except httpx.HTTPError as exc:
            raise AuthenticationError("identity provider is unreachable") from exc

        if response.status_code != 200:
            # Never surface the provider's error body: it can echo the code.
            raise AuthenticationError("authorization code exchange was rejected")

        try:
            body = response.json()
        except ValueError as exc:
            raise AuthenticationError("identity provider returned a malformed response") from exc
        if not isinstance(body, dict):
            raise AuthenticationError("identity provider returned a malformed response")

        id_token = body.get("id_token")
        if not isinstance(id_token, str) or not id_token.strip():
            raise AuthenticationError("identity provider did not return an ID token")

        return self._verify_id_token(id_token, expected_nonce=expected_nonce)

    def _verify_id_token(self, id_token: str, *, expected_nonce: str) -> VerifiedIdToken:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(id_token)
            claims: dict[str, Any] = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=self._algorithms,
                audience=self._client_id,
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
            raise AuthenticationError("identity token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("identity token failed validation") from exc

        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not secrets.compare_digest(nonce, expected_nonce):
            # A missing or mismatched nonce means the token was not minted for
            # this login attempt: a replay or an injected token.
            raise AuthenticationError("identity token nonce does not match this login")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise AuthenticationError("identity token has no subject")

        issued_at = _as_datetime(claims.get("iat"))
        expires_at = _as_datetime(claims.get("exp"))
        if issued_at is None or expires_at is None:
            raise AuthenticationError("identity token has malformed timestamps")

        return VerifiedIdToken(
            issuer=str(claims.get("iss", "")),
            subject=subject,
            audience=str(claims.get("aud", "")),
            nonce=nonce,
            email=claims.get("email") if isinstance(claims.get("email"), str) else None,
            name=claims.get("name") if isinstance(claims.get("name"), str) else None,
            issued_at=issued_at,
            expires_at=expires_at,
        )


def _as_datetime(value: object) -> datetime | None:
    if not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def generate_pkce_verifier() -> str:
    """Generate a fresh PKCE ``code_verifier`` (RFC 7636 §4.1)."""
    return secrets.token_urlsafe(64)


def pkce_challenge_s256(verifier: str) -> str:
    """Derive the S256 challenge: BASE64URL(SHA256(ASCII(verifier))), unpadded."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


__all__ = ["HttpOidcRelyingParty", "generate_pkce_verifier", "pkce_challenge_s256"]
