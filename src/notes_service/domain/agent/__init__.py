"""Agent bounded context: service principals, delegation, and approvals.

Public surface of the domain layer. Agents are first-class actors whose authority
is always derived by attenuation from a human delegator or a service-agent
policy, never self-asserted. See :mod:`notes_service.domain.agent.delegation`
for the attenuation algebra that makes "a child never exceeds its parent" hold.
"""

from notes_service.domain.agent.delegation import DelegatedAuthorization, attenuate
from notes_service.domain.agent.entities import AgentIdentity, ApprovalRequest
from notes_service.domain.agent.value_objects import (
    DEFAULT_MAX_TOKEN_TTL_SECONDS,
    HARD_MAX_TOKEN_TTL_SECONDS,
    NON_DELEGABLE_CAPABILITIES,
    SENSITIVE_CAPABILITIES,
    AgentId,
    AgentStatus,
    ApprovalRequestId,
    ApprovalStatus,
    Capability,
    DelegatedAuthorizationId,
    DelegationStatus,
    RuntimeIdentity,
    TokenAudience,
)

__all__ = [
    "DEFAULT_MAX_TOKEN_TTL_SECONDS",
    "HARD_MAX_TOKEN_TTL_SECONDS",
    "NON_DELEGABLE_CAPABILITIES",
    "SENSITIVE_CAPABILITIES",
    "AgentId",
    "AgentIdentity",
    "AgentStatus",
    "ApprovalRequest",
    "ApprovalRequestId",
    "ApprovalStatus",
    "Capability",
    "DelegatedAuthorization",
    "DelegatedAuthorizationId",
    "DelegationStatus",
    "RuntimeIdentity",
    "TokenAudience",
    "attenuate",
]
