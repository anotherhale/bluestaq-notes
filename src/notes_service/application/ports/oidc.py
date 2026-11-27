"""The OIDC relying-party port.

The application layer drives an authorization-code + PKCE login, but it must not
know how HTTP, JWKS, or JWT signature verification work. This port is the seam:
the use cases build an authorization URL and exchange a code, and an
infrastructure adapter (``HttpOidcRelyingParty``) does the protocol work.

Everything here is framework-free. In particular :class:`VerifiedIdToken` is a
plain dataclass of *already verified* claims: by the time a use case sees one,
the signature, issuer, audience, expiry and nonce have been checked by the
adapter. A use case must still treat a missing or unexpected claim as a denial.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from notes_service.domain.identity.assurance import AuthenticationAssurance


@dataclass(frozen=True, slots=True)
class VerifiedIdToken:
    """Claims from an ID token whose signature and standard claims passed.

    ``subject`` together with the issuer is the stable external identity key.
    ``email`` and ``name`` are informational only: the local user record is the
    authoritative source for those fields, never the IdP.
    """

    issuer: str
    subject: str
    audience: str
    nonce: str | None
    email: str | None
    name: str | None
    issued_at: datetime
    expires_at: datetime
    #: How strongly the IdP asserts the human authenticated, from ``acr``/``amr``
    #: (ADR-015). Derived and bounded by the adapter; never trusted as free text.
    assurance: AuthenticationAssurance = AuthenticationAssurance.SINGLE_FACTOR


@runtime_checkable
class OidcRelyingParty(Protocol):
    """The relying-party operations a login flow needs.

    Implementations MUST verify the ID token fully (signature via JWKS, ``iss``,
    ``aud``, ``exp``, ``iat``, and the expected ``nonce``) and MUST raise
    :class:`~notes_service.application.errors.AuthenticationError` on any
    failure. They must never return partially validated claims.
    """

    @property
    def provider_name(self) -> str: ...

    @property
    def redirect_uri(self) -> str: ...

    def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, login_hint: str | None = None
    ) -> str:
        """Build the IdP authorization endpoint URL for a redirect."""
        ...

    def exchange_code(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> VerifiedIdToken:
        """Redeem an authorization code and return the verified ID token."""
        ...
