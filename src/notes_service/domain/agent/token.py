"""Agent access tokens: the short-lived credential an agent presents.

An :class:`AgentToken` is a *derived* credential. It is never the source of an
agent's authority — the :class:`~notes_service.domain.agent.delegation.DelegatedAuthorization`
is. A token records which delegation it was minted from, so the resolution path
can re-check that delegation on every use and revocation takes effect on the next
request rather than at token expiry.

Like a user session, only the SHA-256 hash of the token secret is persisted: the
raw secret is returned to the caller once and never stored, so a database
disclosure cannot be replayed as a live credential. The input is a full-entropy
random string, so SHA-256 is sufficient (there is no dictionary to attack).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from notes_service.domain.agent.value_objects import (
    HARD_MAX_TOKEN_TTL_SECONDS,
    AgentId,
    AgentTokenId,
    Capability,
    DelegatedAuthorizationId,
    TokenAudience,
)
from notes_service.domain.errors import InvariantViolation, ValidationError

#: Entropy of the token secret. 32 bytes (256 bits) is far beyond any online
#: guessing attack, matching the session-cookie convention.
AGENT_TOKEN_BYTES = 32


def _utcnow() -> datetime:
    return datetime.now(UTC)


def hash_agent_token(token: str) -> str:
    """Return the storage form of an agent token secret."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_agent_token() -> str:
    """Generate a new opaque agent token secret."""
    return secrets.token_urlsafe(AGENT_TOKEN_BYTES)


@dataclass(slots=True)
class AgentToken:
    """One issued access token for an agent, bound to a delegation.

    ``capabilities`` is the attenuated set this token actually carries. It is a
    subset of the delegation's set and therefore of the agent's ceiling; the
    issuance use case computes it through
    :func:`~notes_service.domain.agent.delegation.attenuate`, so a token can
    never carry more than its delegation granted.
    """

    id: AgentTokenId
    agent_id: AgentId
    delegation_id: DelegatedAuthorizationId
    audience: TokenAudience
    capabilities: frozenset[Capability]
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    @classmethod
    def issue(
        cls,
        *,
        agent_id: AgentId,
        delegation_id: DelegatedAuthorizationId,
        audience: TokenAudience,
        capabilities: frozenset[Capability],
        ttl_seconds: int,
        token_id: AgentTokenId | None = None,
        now: datetime | None = None,
    ) -> tuple[AgentToken, str]:
        """Mint a token, returning the aggregate and the raw secret.

        The raw secret is returned exactly once; the caller must hand it to the
        agent and must not persist it. Only the hash is stored on the aggregate.
        """
        if not capabilities:
            raise ValidationError("an agent token must carry at least one capability")
        if not isinstance(audience, TokenAudience):
            raise ValidationError(f"unknown token audience: {audience!r}")
        if not 0 < ttl_seconds <= HARD_MAX_TOKEN_TTL_SECONDS:
            raise ValidationError(
                f"token ttl must be between 1 and {HARD_MAX_TOKEN_TTL_SECONDS} seconds"
            )
        issued = now or _utcnow()
        raw = generate_agent_token()
        token = cls(
            id=token_id or AgentTokenId.new(),
            agent_id=agent_id,
            delegation_id=delegation_id,
            audience=audience,
            capabilities=frozenset(capabilities),
            token_hash=hash_agent_token(raw),
            issued_at=issued,
            expires_at=issued + timedelta(seconds=ttl_seconds),
        )
        return token, raw

    def is_live(self, *, now: datetime | None = None) -> bool:
        moment = now or _utcnow()
        return self.revoked_at is None and self.expires_at > moment

    def assert_live(self, *, now: datetime | None = None) -> None:
        """Fail closed unless this token may still be used."""
        moment = now or _utcnow()
        if self.revoked_at is not None:
            raise InvariantViolation(f"agent token {self.id} has been revoked")
        if self.expires_at <= moment:
            raise InvariantViolation(f"agent token {self.id} has expired")

    def has_capability(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def revoke(self, *, now: datetime | None = None) -> None:
        if self.revoked_at is not None:
            return
        self.revoked_at = now or _utcnow()
