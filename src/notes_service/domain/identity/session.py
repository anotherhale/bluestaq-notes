"""Session and login-transaction aggregates.

Two aggregates make the OIDC authorization-code flow safe:

:class:`LoginTransaction`
    One pending login attempt. It holds the ``state`` the browser must echo, the
    ``nonce`` the ID token must carry, the PKCE ``code_verifier`` and the
    redirect URI the code was issued for. It is **single-use**: consuming it
    twice is a replay and is rejected by the domain, not merely by convention.

:class:`UserSession`
    An established authenticated session. The cookie the browser holds is a
    high-entropy secret; this aggregate stores only its SHA-256 hash, so a
    database leak cannot be turned into session forgery. Sessions expire on both
    an idle timer and an absolute lifetime, and revocation is immediate.

Both are framework-free: they depend on nothing but the standard library and the
domain error taxonomy, so the security rules they encode are unit-testable
without a database or an HTTP client.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.identity.value_objects import (
    LoginTransactionId,
    SessionId,
    UserId,
)

#: Entropy of the cookie secret in bytes. 32 bytes (256 bits) is well beyond
#: what any online guessing attack can reach.
SESSION_TOKEN_BYTES = 32

#: Entropy of the PKCE ``code_verifier``. RFC 7636 requires 43-128 characters;
#: 64 random bytes base64url-encoded gives 86 characters, comfortably inside.
CODE_VERIFIER_BYTES = 64

#: A login attempt that is never completed must not linger. Five minutes is
#: enough for a user to authenticate at the IdP and be redirected back.
LOGIN_TRANSACTION_TTL = timedelta(minutes=5)

_MAX_OPAQUE_VALUE_LENGTH = 512


def _utcnow() -> datetime:
    return datetime.now(UTC)


def hash_session_token(token: str) -> str:
    """Return the storage form of a session cookie secret.

    SHA-256 is sufficient here (unlike for passwords) because the input is a
    full-entropy random token, so there is no dictionary to attack and no need
    for a slow KDF.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    """Generate a new opaque session cookie secret."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def _require_opaque(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must not be empty")
    if len(value) > _MAX_OPAQUE_VALUE_LENGTH:
        raise ValidationError(f"{name} must be at most {_MAX_OPAQUE_VALUE_LENGTH} characters")
    return value


@dataclass(slots=True)
class LoginTransaction:
    """One in-flight OIDC authorization-code login.

    The transaction is created before the browser is redirected to the IdP and
    is looked up again on the callback by ``state``. It binds together every
    value that must survive the round trip, so the callback can verify that the
    code it received was issued for *this* request and this browser.
    """

    id: LoginTransactionId
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    created_at: datetime = field(default_factory=_utcnow)
    expires_at: datetime = field(default_factory=lambda: _utcnow() + LOGIN_TRANSACTION_TTL)
    consumed_at: datetime | None = None

    def __post_init__(self) -> None:
        self.state = _require_opaque(self.state, "state")
        self.nonce = _require_opaque(self.nonce, "nonce")
        self.code_verifier = _require_opaque(self.code_verifier, "code_verifier")
        if not self.redirect_uri or not self.redirect_uri.strip():
            raise ValidationError("redirect_uri must not be empty")
        if self.expires_at <= self.created_at:
            raise ValidationError("expires_at must be after created_at")

    @classmethod
    def begin(
        cls,
        *,
        redirect_uri: str,
        ttl: timedelta = LOGIN_TRANSACTION_TTL,
        now: datetime | None = None,
    ) -> LoginTransaction:
        """Start a new login transaction with fresh cryptographic values."""
        created = now or _utcnow()
        return cls(
            id=LoginTransactionId.new(),
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            code_verifier=secrets.token_urlsafe(CODE_VERIFIER_BYTES),
            redirect_uri=redirect_uri,
            created_at=created,
            expires_at=created + ttl,
        )

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or _utcnow()) >= self.expires_at

    def assert_usable(self, now: datetime | None = None) -> None:
        """Raise unless the transaction may still be completed exactly once."""
        moment = now or _utcnow()
        if self.is_consumed:
            # A second use of the same state is a replay attempt. Treated as an
            # invariant violation so the application layer cannot forget it.
            raise InvariantViolation("login transaction has already been used")
        if self.is_expired(moment):
            raise InvariantViolation("login transaction has expired")

    def consume(self, now: datetime | None = None) -> None:
        """Mark the transaction used. Only valid once, and only while fresh."""
        moment = now or _utcnow()
        self.assert_usable(moment)
        self.consumed_at = moment

    @property
    def code_challenge(self) -> str:
        """The S256 PKCE challenge derived from :attr:`code_verifier`."""
        return pkce_challenge(self.code_verifier)


def pkce_challenge(code_verifier: str) -> str:
    """Derive the RFC 7636 S256 challenge for a verifier.

    ``code_challenge = BASE64URL(SHA256(ASCII(code_verifier)))`` without padding.
    """
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(slots=True)
class UserSession:
    """An established authenticated session.

    ``token_hash`` is the SHA-256 of the cookie secret; the secret itself is
    never stored. A session is valid only while it is neither revoked nor past
    either of its two expiry clocks.
    """

    id: SessionId
    user_id: UserId
    provider: str
    subject: str
    token_hash: str
    created_at: datetime = field(default_factory=_utcnow)
    last_seen_at: datetime = field(default_factory=_utcnow)
    idle_expires_at: datetime = field(default_factory=_utcnow)
    absolute_expires_at: datetime = field(default_factory=_utcnow)
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.provider or not self.provider.strip():
            raise ValidationError("session provider must not be empty")
        if not self.subject or not self.subject.strip():
            raise ValidationError("session subject must not be empty")
        if not self.token_hash or not self.token_hash.strip():
            raise ValidationError("session token_hash must not be empty")

    @classmethod
    def establish(
        cls,
        *,
        user_id: UserId,
        provider: str,
        subject: str,
        token: str,
        idle_ttl: timedelta,
        absolute_ttl: timedelta,
        now: datetime | None = None,
    ) -> UserSession:
        """Create a session for a freshly authenticated user.

        The caller supplies the raw ``token`` (which goes into the cookie); only
        its hash is retained here.
        """
        moment = now or _utcnow()
        if idle_ttl <= timedelta(0):
            raise ValidationError("idle_ttl must be positive")
        if absolute_ttl <= timedelta(0):
            raise ValidationError("absolute_ttl must be positive")
        return cls(
            id=SessionId.new(),
            user_id=user_id,
            provider=provider,
            subject=subject,
            token_hash=hash_session_token(token),
            created_at=moment,
            last_seen_at=moment,
            idle_expires_at=moment + idle_ttl,
            absolute_expires_at=moment + absolute_ttl,
        )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_active(self, now: datetime | None = None) -> bool:
        """True only while the session is live on both clocks and unrevoked."""
        moment = now or _utcnow()
        if self.is_revoked:
            return False
        return moment < self.idle_expires_at and moment < self.absolute_expires_at

    def touch(self, now: datetime | None = None, *, idle_ttl: timedelta | None = None) -> None:
        """Record activity and slide the idle window, never past the absolute cap."""
        moment = now or _utcnow()
        if not self.is_active(moment):
            return
        if idle_ttl is None:
            original_idle = self.idle_expires_at - self.last_seen_at
            idle_ttl = original_idle if original_idle > timedelta(0) else timedelta(0)
        self.last_seen_at = moment
        self.idle_expires_at = min(moment + idle_ttl, self.absolute_expires_at)

    def revoke(self, now: datetime | None = None) -> None:
        """Revoke the session. Idempotent: the first revocation time is kept."""
        if self.revoked_at is None:
            self.revoked_at = now or _utcnow()

    def with_token_hash(self, token_hash: str) -> UserSession:
        return replace(self, token_hash=token_hash)


__all__ = [
    "CODE_VERIFIER_BYTES",
    "LOGIN_TRANSACTION_TTL",
    "SESSION_TOKEN_BYTES",
    "LoginTransaction",
    "UserSession",
    "generate_session_token",
    "hash_session_token",
    "pkce_challenge",
]
