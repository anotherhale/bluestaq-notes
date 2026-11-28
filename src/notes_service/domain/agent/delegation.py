"""Delegation and attenuation: the core security algebra of the agent context.

Every rule here exists to make one property hold: **a delegated authority is
never larger than the authority that created it.** The function
:func:`attenuate` is the single place that narrows a capability set, and it is
written to fail closed — an unknown or non-delegable capability raises rather
than being silently dropped, because silent dropping hides an escalation attempt
from the audit trail.

The exhaustive sweep in ``tests/agent/test_delegation.py`` exercises every
parent/child capability pair, including chains of arbitrary depth, against this
function; if a future change lets authority grow at any hop, that test fails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from notes_service.domain.agent.entities import AgentIdentity
from notes_service.domain.agent.value_objects import (
    NON_DELEGABLE_CAPABILITIES,
    AgentId,
    Capability,
    DelegatedAuthorizationId,
    DelegationStatus,
)
from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.identity.assurance import AuthenticationAssurance
from notes_service.domain.identity.value_objects import UserId


def _utcnow() -> datetime:
    return datetime.now(UTC)


def attenuate(
    *,
    granted: frozenset[Capability],
    requested: frozenset[Capability],
    require_delegable: bool = True,
) -> frozenset[Capability]:
    """Return ``requested`` if it is a subset of ``granted``, else raise.

    ``granted`` is the parent authority; ``requested`` is what a child hop asks
    for. The result is exactly ``requested`` so a caller can narrow without
    accidentally widening back to the parent set.

    ``require_delegable`` rejects :data:`NON_DELEGABLE_CAPABILITIES`, which are
    reachable only through service-agent policy, never through delegation.
    """
    if not requested:
        raise ValidationError("a delegated authority must request at least one capability")

    excess = requested - granted
    if excess:
        raise InvariantViolation(
            "delegation exceeds the agent's capabilities: "
            + ", ".join(sorted(c.value for c in excess))
        )

    if require_delegable:
        forbidden = requested & NON_DELEGABLE_CAPABILITIES
        if forbidden:
            raise InvariantViolation(
                "capabilities are not delegable by a user: "
                + ", ".join(sorted(c.value for c in forbidden))
            )

    return frozenset(requested)


@dataclass(slots=True)
class DelegatedAuthorization:
    """One human's consent for one agent to act, narrowed to a capability set.

    A delegation is the *source* of an agent's authority for a given user. It is
    revocable, and revocation must invalidate tokens derived from it on the next
    request — so the resolution path always re-checks the delegation, never just
    the token.
    """

    id: DelegatedAuthorizationId
    agent_id: AgentId
    delegator_user_id: UserId
    capabilities: frozenset[Capability]
    status: DelegationStatus = DelegationStatus.ACTIVE
    created_at: datetime = field(default_factory=_utcnow)
    revoked_at: datetime | None = None
    # How strongly the delegating human authenticated when they granted this
    # authority (ADR-015). Fixed at consent: the delegation may outlive the
    # session, so this is recorded here rather than read live from the session
    # store on every gateway call.
    assurance: AuthenticationAssurance = AuthenticationAssurance.SINGLE_FACTOR

    @classmethod
    def grant(
        cls,
        *,
        agent: AgentIdentity,
        delegator_user_id: UserId | str,
        capabilities: frozenset[Capability] | set[Capability],
        authorization_id: DelegatedAuthorizationId | None = None,
        assurance: AuthenticationAssurance = AuthenticationAssurance.SINGLE_FACTOR,
    ) -> DelegatedAuthorization:
        narrowed = attenuate(
            granted=agent.capabilities,
            requested=frozenset(capabilities),
            require_delegable=True,
        )
        return cls(
            id=authorization_id or DelegatedAuthorizationId.new(),
            agent_id=agent.id,
            delegator_user_id=UserId.parse(delegator_user_id),
            capabilities=narrowed,
            assurance=assurance,
        )

    def is_active(self) -> bool:
        return self.status is DelegationStatus.ACTIVE

    def revoke(self, *, now: datetime | None = None) -> None:
        if self.status is DelegationStatus.REVOKED:
            return
        self.status = DelegationStatus.REVOKED
        self.revoked_at = now or _utcnow()

    def assert_usable(self) -> None:
        """Fail closed unless this delegation may still authorise work."""
        if not self.is_active():
            raise InvariantViolation(
                f"delegated authorization {self.id} is revoked and grants no authority"
            )
