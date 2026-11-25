"""Agent aggregate: the service principal and its human-approval workflow.

The design rule this module enforces is that **an agent's authority is never
self-asserted**. An :class:`AgentIdentity` carries a capability ceiling, and a
:class:`ApprovalRequest` records that a *human* — not the agent — authorised a
sensitive action. Both are small, self-validating aggregates so a bug in a use
case cannot persist an escalation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from notes_service.domain.agent.value_objects import (
    DEFAULT_MAX_TOKEN_TTL_SECONDS,
    HARD_MAX_TOKEN_TTL_SECONDS,
    SENSITIVE_CAPABILITIES,
    AgentId,
    AgentStatus,
    ApprovalRequestId,
    ApprovalStatus,
    Capability,
    RuntimeIdentity,
    TokenAudience,
)
from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.identity.value_objects import UserId

_MAX_DISPLAY_NAME = 120

# An approval authorises one sensitive action, once, and not indefinitely.
# The security architecture caps approval validity and requires that an expired
# approval block execution (ENTERPRISE_SECURITY_ARCHITECTURE_V1 §11). Sensitive
# agent tools sit at the High tier, whose ceiling is 72 hours; this is the
# platform maximum, and tenant policy may only shorten it.
DEFAULT_APPROVAL_TTL = timedelta(hours=72)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Versioned:
    """Optimistic-concurrency mixin; every mutation bumps ``version``."""

    version: int
    updated_at: datetime

    def _bump(self, now: datetime | None) -> None:
        self.version += 1
        self.updated_at = now or _utcnow()


def _normalise_capabilities(
    capabilities: frozenset[Capability] | set[Capability],
) -> frozenset[Capability]:
    if not capabilities:
        raise ValidationError("an agent must have at least one capability")
    return frozenset(capabilities)


@dataclass(slots=True)
class AgentIdentity(_Versioned):
    """A registered service principal owned by a human user.

    ``capabilities`` is a *ceiling*, not a grant. Issuing a token narrows it
    further via :func:`~notes_service.domain.agent.delegation.attenuate`; the
    ceiling is what a delegator's request is validated against.
    """

    id: AgentId
    display_name: str
    owner_user_id: UserId
    runtime_identity: RuntimeIdentity
    capabilities: frozenset[Capability]
    status: AgentStatus = AgentStatus.ACTIVE
    max_token_ttl_seconds: int = DEFAULT_MAX_TOKEN_TTL_SECONDS
    version: int = 1
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    revoked_at: datetime | None = None

    @classmethod
    def register(
        cls,
        *,
        display_name: str,
        owner_user_id: UserId | str,
        runtime_identity: RuntimeIdentity | str,
        capabilities: frozenset[Capability] | set[Capability],
        agent_id: AgentId | None = None,
        max_token_ttl_seconds: int = DEFAULT_MAX_TOKEN_TTL_SECONDS,
    ) -> AgentIdentity:
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValidationError("agent display name must not be empty")
        trimmed = display_name.strip()
        if len(trimmed) > _MAX_DISPLAY_NAME:
            raise ValidationError(
                f"agent display name must be at most {_MAX_DISPLAY_NAME} characters"
            )
        if not 0 < max_token_ttl_seconds <= HARD_MAX_TOKEN_TTL_SECONDS:
            raise ValidationError(
                f"max token ttl must be between 1 and {HARD_MAX_TOKEN_TTL_SECONDS} seconds"
            )
        return cls(
            id=agent_id or AgentId.new(),
            display_name=trimmed,
            owner_user_id=UserId.parse(owner_user_id),
            runtime_identity=RuntimeIdentity(runtime_identity),
            capabilities=_normalise_capabilities(capabilities),
            max_token_ttl_seconds=max_token_ttl_seconds,
        )

    @property
    def is_operational(self) -> bool:
        return self.status.is_operational

    def assert_can_issue(self, *, audience: TokenAudience, ttl_seconds: int) -> None:
        """Fail closed unless this agent may be issued a token right now.

        Called on *every* issuance and on *every* token use (via the caller's
        resolution path), so revocation takes effect on the next request rather
        than at token expiry.
        """
        if not self.is_operational:
            raise InvariantViolation(
                f"agent {self.id} is {self.status.value} and cannot issue tokens"
            )
        if not isinstance(audience, TokenAudience):
            raise ValidationError(f"unknown token audience: {audience!r}")
        if ttl_seconds <= 0:
            raise ValidationError("token ttl must be positive")
        if ttl_seconds > min(self.max_token_ttl_seconds, HARD_MAX_TOKEN_TTL_SECONDS):
            raise ValidationError(
                f"token ttl {ttl_seconds}s exceeds the agent's maximum of "
                f"{min(self.max_token_ttl_seconds, HARD_MAX_TOKEN_TTL_SECONDS)}s"
            )

    def has_capability(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def suspend(self, *, now: datetime | None = None) -> None:
        if self.status is AgentStatus.REVOKED:
            raise InvariantViolation("a revoked agent cannot be suspended")
        self.status = AgentStatus.SUSPENDED
        self._bump(now)

    def activate(self, *, now: datetime | None = None) -> None:
        if self.status is AgentStatus.REVOKED:
            raise InvariantViolation("a revoked agent cannot be reactivated")
        self.status = AgentStatus.ACTIVE
        self._bump(now)

    def revoke(self, *, now: datetime | None = None) -> None:
        """Terminal emergency stop. Idempotent; a revoked agent stays revoked."""
        if self.status is AgentStatus.REVOKED:
            return
        self.status = AgentStatus.REVOKED
        self.revoked_at = now or _utcnow()
        self._bump(now)

    def narrow_capabilities(
        self, *, capabilities: frozenset[Capability] | set[Capability], now: datetime | None = None
    ) -> None:
        """Reduce the ceiling. Widening is rejected: an edit must not escalate."""
        requested = _normalise_capabilities(capabilities)
        added = requested - self.capabilities
        if added:
            raise InvariantViolation(
                "agent capabilities may only be narrowed; cannot add: "
                + ", ".join(sorted(c.value for c in added))
            )
        self.capabilities = requested
        self._bump(now)


@dataclass(slots=True)
class ApprovalRequest:
    """A human decision on one sensitive action an agent wants to take.

    The agent opens the request; only a distinct human may decide it. The
    separation-of-duties check lives here, in the aggregate, so no use case can
    forget it.
    """

    id: ApprovalRequestId
    agent_id: AgentId
    capability: Capability
    resource_ref: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = field(default_factory=_utcnow)
    decided_at: datetime | None = None
    decided_by_user_id: UserId | None = None
    reason: str | None = None
    expires_at: datetime | None = None
    consumed_at: datetime | None = None

    @classmethod
    def open(
        cls,
        *,
        agent: AgentIdentity,
        capability: Capability,
        resource_ref: str,
        approval_id: ApprovalRequestId | None = None,
        ttl: timedelta = DEFAULT_APPROVAL_TTL,
        now: datetime | None = None,
    ) -> ApprovalRequest:
        if capability not in SENSITIVE_CAPABILITIES:
            raise InvariantViolation(
                f"capability {capability.value} is not sensitive and needs no approval"
            )
        if not agent.has_capability(capability):
            raise InvariantViolation(
                f"agent {agent.id} lacks capability {capability.value} and cannot request approval"
            )
        if not isinstance(resource_ref, str) or not resource_ref.strip():
            raise ValidationError("approval request must name a resource")
        if ttl <= timedelta(0):
            raise ValidationError("approval validity must be positive")
        opened_at = now or _utcnow()
        return cls(
            id=approval_id or ApprovalRequestId.new(),
            agent_id=agent.id,
            capability=capability,
            resource_ref=resource_ref.strip(),
            requested_at=opened_at,
            expires_at=opened_at + ttl,
        )

    @property
    def is_pending(self) -> bool:
        return self.status is ApprovalStatus.PENDING

    @property
    def is_expired(self) -> bool:
        # Fail closed on a missing bound: an approval whose validity was never
        # recorded is not an approval with unlimited validity. Reading NULL as
        # "never expires" is how an unpersisted column silently becomes
        # permanent authority, so the absent case is treated as lapsed.
        return self.expires_at is None or _utcnow() >= self.expires_at

    def is_usable(self, *, now: datetime | None = None) -> bool:
        """Whether this approval may authorise an action right now.

        Three independent conditions, all required: it was approved, nobody has
        spent it yet, and it has not expired. They are separate statuses in the
        security architecture (expired / revoked / invalidated all block
        execution), so they are checked separately here rather than collapsed
        into one boolean that a caller could misread.

        A missing ``expires_at`` counts as expired, not as unbounded. The column
        is nullable only for rows written before it existed, and the security
        architecture requires those to become unusable rather than retain
        indefinite authority.
        """
        if self.status is not ApprovalStatus.APPROVED:
            return False
        if self.consumed_at is not None:
            return False
        if self.expires_at is None:
            return False
        moment = now or _utcnow()
        return moment < self.expires_at

    def _assert_decidable(self, approver_user_id: UserId | None) -> UserId:
        if approver_user_id is None:
            raise InvariantViolation(
                "an agent cannot approve its own action; a human approver is required"
            )
        if self.status is not ApprovalStatus.PENDING:
            raise InvariantViolation(f"approval request is already {self.status.value}")
        if self.is_expired:
            # Deciding an expired request would produce an APPROVED row that the
            # gateway must then refuse; refuse the decision instead, so the
            # recorded status never claims an authority that has lapsed.
            raise InvariantViolation("approval request has expired and cannot be decided")
        return approver_user_id

    def approve(
        self,
        *,
        approver_user_id: UserId | str | None,
        now: datetime | None = None,
        reason: str | None = None,
    ) -> None:
        approver = UserId.parse(approver_user_id) if approver_user_id is not None else None
        self._assert_decidable(approver)
        self.status = ApprovalStatus.APPROVED
        self.decided_by_user_id = approver
        self.decided_at = now or _utcnow()
        self.reason = reason

    def deny(
        self,
        *,
        approver_user_id: UserId | str | None,
        now: datetime | None = None,
        reason: str | None = None,
    ) -> None:
        approver = UserId.parse(approver_user_id) if approver_user_id is not None else None
        self._assert_decidable(approver)
        self.status = ApprovalStatus.DENIED
        self.decided_by_user_id = approver
        self.decided_at = now or _utcnow()
        self.reason = reason

    def consume(self, *, now: datetime | None = None) -> None:
        """Spend this approval on exactly one action.

        An approval is an authorisation for one sensitive act, not a standing
        permission. Without this, a single human decision to delete one note
        would silently authorise every later delete of that note for as long as
        the request row lived. Consuming stamps ``consumed_at`` so the gateway's
        ``is_usable`` check fails for every subsequent attempt.
        """
        if not self.is_usable(now=now):
            raise InvariantViolation(
                f"approval request {self.id} is not usable and cannot be consumed"
            )
        self.consumed_at = now or _utcnow()
