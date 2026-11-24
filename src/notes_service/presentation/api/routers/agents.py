"""Agent identity endpoints under ``/api/v1/agents``.

This is the surface through which a human registers a service principal, grants
it a narrowed delegation, exchanges that delegation for a short-lived token, and
authorises (or refuses) a sensitive action the agent has asked for.

Every authorization decision is delegated to the application layer. The router
maps HTTP to use cases and nothing more, with three consequences worth stating:

* **A non-owner sees 404, not 403.** An agent's existence is itself private, so
  an agent id cannot be probed. The application layer raises ``ResourceNotFound``
  for a missing *or* unowned agent.
* **No identity is taken from the body.** The owner and the delegator are always
  the authenticated principal, so no request can act on another user's behalf.
* **A token secret is returned exactly once.** ``POST .../tokens`` is the only
  response that carries it; resolution accepts a secret but never echoes one.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter, Response, status

from notes_service.presentation.dependencies import ContainerDep, PrincipalDep, RateLimitDep
from notes_service.presentation.schemas import (
    AgentListResponse,
    AgentRegisterRequest,
    AgentResponse,
    AgentTokenIssueRequest,
    AgentTokenResolveRequest,
    AgentTokenResponse,
    ApprovalDecisionRequest,
    ApprovalRequestListResponse,
    ApprovalRequestOpenRequest,
    ApprovalRequestResponse,
    DelegationGrantRequest,
    DelegationResponse,
    PageMeta,
    ResolvedAgentTokenResponse,
)

router = APIRouter(prefix="/agents", tags=["agents"])


def _page(items: Sequence[object]) -> PageMeta:
    """These listings are unpaginated, so the meta describes the full set."""
    return PageMeta(total=len(items), limit=len(items), offset=0)


# -- agents ---------------------------------------------------------------


@router.get(
    "",
    response_model=AgentListResponse,
    summary="List the agents the caller owns",
    dependencies=[RateLimitDep],
)
def list_agents(principal: PrincipalDep, container: ContainerDep) -> AgentListResponse:
    agents = container.list_agents().run(principal)
    items = [AgentResponse.from_domain(agent) for agent in agents]
    return AgentListResponse(items=items, meta=_page(items))


@router.post(
    "",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register an agent owned by the caller",
    dependencies=[RateLimitDep],
)
def register_agent(
    payload: AgentRegisterRequest, principal: PrincipalDep, container: ContainerDep
) -> AgentResponse:
    agent = container.register_agent().run(
        principal,
        display_name=payload.display_name,
        runtime_identity=payload.runtime_identity,
        capabilities=[capability.value for capability in payload.capabilities],
        max_token_ttl_seconds=payload.max_token_ttl_seconds,
    )
    return AgentResponse.from_domain(agent)


@router.get(
    "/{agent_id}",
    response_model=AgentResponse,
    summary="Fetch one of the caller's agents",
    dependencies=[RateLimitDep],
)
def get_agent(agent_id: UUID, principal: PrincipalDep, container: ContainerDep) -> AgentResponse:
    agent = container.get_agent().run(principal, agent_id=str(agent_id))
    return AgentResponse.from_domain(agent)


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an agent, its delegations, and its tokens (emergency stop)",
    dependencies=[RateLimitDep],
)
def revoke_agent(agent_id: UUID, principal: PrincipalDep, container: ContainerDep) -> Response:
    container.revoke_agent().run(principal, agent_id=str(agent_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- delegations ----------------------------------------------------------


@router.post(
    "/{agent_id}/delegations",
    response_model=DelegationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a delegation, attenuated to the agent's ceiling",
    dependencies=[RateLimitDep],
)
def grant_delegation(
    agent_id: UUID,
    payload: DelegationGrantRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> DelegationResponse:
    delegation = container.grant_delegation().run(
        principal,
        agent_id=str(agent_id),
        capabilities=[capability.value for capability in payload.capabilities],
    )
    return DelegationResponse.from_domain(delegation)


@router.delete(
    "/delegations/{delegation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a delegation and every token derived from it",
    dependencies=[RateLimitDep],
)
def revoke_delegation(
    delegation_id: UUID, principal: PrincipalDep, container: ContainerDep
) -> Response:
    container.revoke_delegation().run(principal, delegation_id=str(delegation_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- tokens ---------------------------------------------------------------


@router.post(
    "/delegations/{delegation_id}/tokens",
    response_model=AgentTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Exchange a delegation for a short-lived, narrower token",
    dependencies=[RateLimitDep],
)
def issue_agent_token(
    delegation_id: UUID,
    payload: AgentTokenIssueRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> AgentTokenResponse:
    issued = container.issue_agent_token().run(
        principal,
        delegation_id=str(delegation_id),
        capabilities=[capability.value for capability in payload.capabilities],
        audience=payload.audience.value,
        ttl_seconds=payload.ttl_seconds,
    )
    return AgentTokenResponse.from_issued(issued)


@router.post(
    "/tokens/resolve",
    response_model=ResolvedAgentTokenResponse,
    summary="Resolve a presented token secret to its agent (token introspection)",
    dependencies=[RateLimitDep],
)
def resolve_agent_token(
    payload: AgentTokenResolveRequest, principal: PrincipalDep, container: ContainerDep
) -> ResolvedAgentTokenResponse:
    resolved = container.resolve_agent_token().run(
        secret=payload.secret, audience=payload.audience.value
    )
    return ResolvedAgentTokenResponse.from_resolved(resolved)


# -- approvals ------------------------------------------------------------


@router.post(
    "/{agent_id}/approvals",
    response_model=ApprovalRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a human approval request for one sensitive action",
    dependencies=[RateLimitDep],
)
def open_approval_request(
    agent_id: UUID,
    payload: ApprovalRequestOpenRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> ApprovalRequestResponse:
    request = container.open_approval_request().run(
        principal,
        agent_id=str(agent_id),
        capability=payload.capability.value,
        resource_ref=payload.resource_ref,
    )
    return ApprovalRequestResponse.from_domain(request)


@router.get(
    "/{agent_id}/approvals",
    response_model=ApprovalRequestListResponse,
    summary="List pending approvals for one of the caller's agents",
    dependencies=[RateLimitDep],
)
def list_approval_requests(
    agent_id: UUID, principal: PrincipalDep, container: ContainerDep
) -> ApprovalRequestListResponse:
    requests = container.list_approval_requests().run(principal, agent_id=str(agent_id))
    items = [ApprovalRequestResponse.from_domain(request) for request in requests]
    return ApprovalRequestListResponse(items=items, meta=_page(items))


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalRequestResponse,
    summary="Approve or deny a pending approval request (human only)",
    dependencies=[RateLimitDep],
)
def decide_approval_request(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> ApprovalRequestResponse:
    request = container.decide_approval_request().run(
        principal,
        approval_id=str(approval_id),
        approve=payload.approve,
        reason=payload.reason,
    )
    return ApprovalRequestResponse.from_domain(request)
