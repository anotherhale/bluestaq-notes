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
import re
import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from notes_service.application.errors import AuthenticationError
from notes_service.application.ports.oidc import VerifiedIdToken
from notes_service.domain.identity.assurance import AuthenticationAssurance

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
        if not isinstance(nonce, str) or not secrets.compare_digest(
            nonce.encode("utf-8", "surrogatepass"), expected_nonce.encode("utf-8")
        ):
            # A missing or mismatched nonce means the token was not minted for
            # this login attempt: a replay or an injected token. Comparing the
            # UTF-8 encodings keeps this constant-time for any claim value;
            # compare_digest on str raises TypeError for non-ASCII, which would
            # turn a hostile token into a 500 instead of a refusal.
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
            assurance=derive_assurance(claims),
        )


#: ``amr`` methods that count as a second, independent factor. Kept as a closed
#: set so a provider cannot smuggle a weaker method in under a new spelling and
#: have it read as multi-factor. A password/OIDC login is always one factor; a
#: second entry from this set is what makes a token multi-factor (ADR-015).
_MULTI_FACTOR_AMR: frozenset[str] = frozenset(
    {
        "mfa",
        "otp",
        "totp",
        "hwk",
        "swk",
        "sms",
        "tel",
        "fpt",
        "face",
        "iris",
        "vbm",
        "pin",
        "pwd-2fa",
        "webauthn",
        "fido",
        "passkey",
    }
)


#: Tokens that separate the segments of an ``acr`` value. An ``acr`` is a class
#: name (``urn:acme:multi-factor``, ``https://idp/loa/mfa``), so a marker has to
#: be matched as a whole segment rather than as a substring of one.
_ACR_SEPARATOR = re.compile(r"[^a-z0-9]+")

#: Segments that invert the meaning of a nearby marker. ``mfa-optional``,
#: ``mfa-not-required`` and ``not-multifactor`` all name multi-factor in order to
#: say it was *not* performed, and a substring match read every one of them as
#: stepped up. In an enforcement path that is the difference between granting and
#: refusing authority, so a marker next to one of these is not counted.
_ACR_NEGATIONS: frozenset[str] = frozenset(
    {
        "absent",
        "bypass",
        "bypassed",
        "disabled",
        "never",
        "no",
        "none",
        "not",
        "off",
        "optional",
        "skip",
        "skipped",
        "unsupported",
        "without",
    }
)

#: ``acr`` segments that name a multi-factor level on their own.
_ACR_MULTI_FACTOR_TOKENS: frozenset[str] = frozenset({"mfa", "multifactor"})


def _acr_tokens(acr: str) -> list[str]:
    return [token for token in _ACR_SEPARATOR.split(acr.strip().lower()) if token]


def _marker_is_negated(tokens: list[str], start: int, span: int) -> bool:
    before = tokens[start - 1] if start > 0 else None
    after_index = start + span
    after = tokens[after_index] if after_index < len(tokens) else None
    return before in _ACR_NEGATIONS or after in _ACR_NEGATIONS


def _acr_names_multi_factor(acr: str) -> bool:
    """True when ``acr`` positively asserts a multi-factor login.

    Matched by segment, with an adjacent negation vetoing the marker. The veto is
    deliberately blunt: a compound level such as ``mfa:not-sms`` is read as
    single-factor because this function cannot tell "multi-factor, but not by SMS"
    from "multi-factor not required" without a registry of level names it does not
    have. Understating refuses a step-up; overstating spends one on a login that
    never happened, so the blunt direction is the safe one.
    """
    tokens = _acr_tokens(acr)
    for index, token in enumerate(tokens):
        if token in _ACR_MULTI_FACTOR_TOKENS and not _marker_is_negated(tokens, index, 1):
            return True
        if (
            token == "multi"
            and index + 1 < len(tokens)
            and tokens[index + 1] == "factor"
            and not _marker_is_negated(tokens, index, 2)
        ):
            return True
    return False


def derive_assurance(claims: dict[str, Any]) -> AuthenticationAssurance:
    """Derive authentication assurance from verified ``acr``/``amr`` claims.

    Only two outcomes exist, and the default is the weaker one, so anything this
    function does not positively recognise is single-factor. That matters: the
    claims here have passed signature and standard-claim verification, but their
    *content* is still an assertion by the IdP, and a provider that sends an
    ``amr`` shape we do not model must not be read as stronger than it is.

    Two independent signals, either of which is sufficient:

    * ``amr`` lists a second factor from :data:`_MULTI_FACTOR_AMR` alongside a
      first one, or
    * ``acr`` names a recognised multi-factor level, matched by segment and
      refused when a neighbouring segment negates it (see
      :func:`_acr_names_multi_factor`).

    Numeric NIST level conventions (``aal2``, ``loa3``, ``urn:okta:loa:2``) are
    deliberately **not** mapped. Several of them describe identity proofing rather
    than the number of authentication factors, so treating a numeric level as
    multi-factor would guess at a semantics that differs between providers. They
    therefore read as single-factor, which refuses a step-up rather than granting
    one on an assumption. Mapping them is a vocabulary change and needs an ADR.
    """
    acr = claims.get("acr")
    if isinstance(acr, str) and acr.strip() and _acr_names_multi_factor(acr):
        return AuthenticationAssurance.MULTI_FACTOR

    amr = claims.get("amr")
    if isinstance(amr, str):
        methods = {amr.strip().lower()}
    elif isinstance(amr, list | tuple):
        methods = {str(item).strip().lower() for item in amr if isinstance(item, str)}
    else:
        methods = set()
    if len(methods) >= 2 and methods & _MULTI_FACTOR_AMR:
        return AuthenticationAssurance.MULTI_FACTOR

    return AuthenticationAssurance.SINGLE_FACTOR


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
