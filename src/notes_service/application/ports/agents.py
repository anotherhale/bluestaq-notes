"""Persistence ports for the agent bounded context.

Agents, their delegations, issued tokens, and approval requests are persisted
through these ports. None of them commits: the Unit of Work owns transaction
boundaries, exactly as for every other repository port.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from notes_service.domain.agent.delegation import DelegatedAuthorization
from notes_service.domain.agent.entities import AgentIdentity, ApprovalRequest
from notes_service.domain.agent.token import AgentToken
from notes_service.domain.agent.value_objects import (
    AgentId,
    ApprovalRequestId,
    DelegatedAuthorizationId,
)
from notes_service.domain.identity.value_objects import UserId


@runtime_checkable
class AgentRepository(Protocol):
    """Persistence port for agent principals."""

    def add(self, agent: AgentIdentity) -> None: ...

    def get(self, agent_id: AgentId) -> AgentIdentity | None: ...

    def get_by_runtime_identity(self, runtime_identity: str) -> AgentIdentity | None: ...

    def list_for_owner(self, owner_user_id: UserId) -> list[AgentIdentity]:
        """Agents owned by one user.

        There is deliberately no unscoped ``list_all``: a caller must always
        name the owner, so one user cannot enumerate another's agents.
        """
        ...

    def save(self, agent: AgentIdentity) -> None: ...


@runtime_checkable
class DelegationRepository(Protocol):
    """Persistence port for delegated authorizations."""

    def add(self, delegation: DelegatedAuthorization) -> None: ...

    def get(self, delegation_id: DelegatedAuthorizationId) -> DelegatedAuthorization | None: ...

    def list_for_agent(self, agent_id: AgentId) -> list[DelegatedAuthorization]: ...

    def list_active_for_delegator(self, user_id: UserId) -> list[DelegatedAuthorization]: ...

    def save(self, delegation: DelegatedAuthorization) -> None: ...

    def revoke_all_for_agent(self, agent_id: AgentId, *, now: datetime) -> int:
        """Revoke every active delegation for an agent; returns the count.

        Used when an agent is revoked so derived tokens lose their authority
        immediately rather than at token expiry.
        """
        ...


@runtime_checkable
class AgentTokenRepository(Protocol):
    """Persistence port for issued agent tokens.

    Lookups are by token *hash* only: the raw secret never reaches the
    repository, so it cannot be logged or persisted by accident.
    """

    def add(self, token: AgentToken) -> None: ...

    def get_by_token_hash(self, token_hash: str) -> AgentToken | None: ...

    def save(self, token: AgentToken) -> None: ...

    def revoke_all_for_delegation(
        self, delegation_id: DelegatedAuthorizationId, *, now: datetime
    ) -> int: ...

    def revoke_all_for_agent(self, agent_id: AgentId, *, now: datetime) -> int: ...


@runtime_checkable
class ApprovalRequestRepository(Protocol):
    """Persistence port for human approvals of sensitive agent actions."""

    def add(self, request: ApprovalRequest) -> None: ...

    def get(self, approval_id: ApprovalRequestId) -> ApprovalRequest | None: ...

    def list_pending_for_agent(self, agent_id: AgentId) -> list[ApprovalRequest]: ...

    def save(self, request: ApprovalRequest) -> None: ...


__all__ = [
    "AgentRepository",
    "AgentTokenRepository",
    "ApprovalRequestRepository",
    "DelegationRepository",
]
