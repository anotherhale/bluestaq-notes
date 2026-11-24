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
from notes_service.domain.agent.tools import ToolInvocation, ToolInvocationId
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

    def list_for_agent(self, agent_id: AgentId) -> list[ApprovalRequest]:
        """Every approval request for an agent, newest state included.

        The tool gateway needs *approved* requests, not only pending ones, so
        this is broader than :meth:`list_pending_for_agent`.
        """
        ...

    def save(self, request: ApprovalRequest) -> None: ...


@runtime_checkable
class ToolInvocationRepository(Protocol):
    """Persistence port for the tool-invocation audit trail.

    A record is written before the handler runs and finalised afterwards, so
    this repository must support an insert followed by an in-place update within
    one transaction. Lookups are scoped by agent: an invocation is never
    readable across agents.
    """

    def add(self, invocation: ToolInvocation) -> None: ...

    def save(self, invocation: ToolInvocation) -> None: ...

    def get(self, invocation_id: ToolInvocationId) -> ToolInvocation | None: ...

    def find_by_idempotency_key(
        self, agent_id: AgentId, idempotency_key: str
    ) -> ToolInvocation | None:
        """The invocation recorded for this agent under this key, if any.

        Scoped to the agent, not global: one agent reusing a key must not be
        able to observe or block another agent's invocation.
        """
        ...

    def list_for_agent(
        self, agent_id: AgentId, *, limit: int = 50, offset: int = 0
    ) -> list[ToolInvocation]: ...


__all__ = [
    "AgentRepository",
    "AgentTokenRepository",
    "ApprovalRequestRepository",
    "DelegationRepository",
    "ToolInvocationRepository",
]
