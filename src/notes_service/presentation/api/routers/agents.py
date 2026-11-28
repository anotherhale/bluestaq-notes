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

from fastapi import APIRouter, Query, Response, status

from notes_service.domain.agent.value_objects import ApprovalStatus
from notes_service.presentation.dependencies import ContainerDep, PrincipalDep, RateLimitDep
from notes_service.presentation.schemas import (
    AgentListResponse,
    AgentRegisterRequest,
    AgentResponse,
    AgentTokenIssueRequest,
    AgentTokenResolveRequest,
    AgentTokenResponse,
    ApprovalAdvisoryResponse,
    ApprovalDecisionRequest,
    ApprovalRequestListResponse,
    ApprovalRequestOpenRequest,
    ApprovalRequestResponse,
    ApprovalRevokeRequest,
    ChainVerdictResponse,
    DelegationGrantRequest,
    DelegationResponse,
    PageMeta,
    ResolvedAgentTokenResponse,
    ToolInvocationAuditListResponse,
    ToolInvocationAuditResponse,
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
    summary="List approvals for one of the caller's agents, pending by default",
    dependencies=[RateLimitDep],
)
def list_approval_requests(
    agent_id: UUID,
    principal: PrincipalDep,
    container: ContainerDep,
    status: ApprovalStatus = Query(
        default=ApprovalStatus.PENDING,
        description=(
            "Which state to list. Defaults to pending, the actionable queue. Any "
            "other value reads the agent's full history so a decision stays reviewable."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApprovalRequestListResponse:
    """Approvals this agent opened, one status at a time.

    A non-owner gets 404, never 403, so the route cannot probe for another user's
    agent. Paging is explicit here rather than the unpaginated ``_page`` helper:
    an agent's approval history is never pruned, so a fixed default window keeps
    one request from loading every approval it ever opened. The status filter and
    the window are applied in the store.

    ``meta.total`` is the number of approvals in this status, not of ``items``. A
    window that reported its own size would make "3 denied approvals" and "the
    first 3 of 300" indistinguishable, which is exactly what an auditor must be
    able to tell apart.
    """
    page = container.list_approval_requests().run(
        principal,
        agent_id=str(agent_id),
        status=status.value,
        limit=limit,
        offset=offset,
    )
    items = [ApprovalRequestResponse.from_domain(request) for request in page.items]
    return ApprovalRequestListResponse(
        items=items, meta=PageMeta(total=page.total, limit=limit, offset=offset)
    )


@router.get(
    "/{agent_id}/tool-invocations",
    response_model=ToolInvocationAuditListResponse,
    summary="Read the tool-invocation audit trail for one of the caller's agents",
    dependencies=[RateLimitDep],
)
def list_tool_invocations(
    agent_id: UUID,
    principal: PrincipalDep,
    container: ContainerDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ToolInvocationAuditListResponse:
    """Every attempt this agent made, refusals included.

    The trail is the owner's evidence, so a non-owner gets 404, never 403. Paging
    is explicit here rather than the unpaginated ``_page`` helper used by the
    other listings: an audit trail grows without bound, so a fixed default window
    keeps one request from returning an agent's entire history.

    ``meta.total`` is the length of the whole trail, not of ``items``. A window
    that reports its own size as the total cannot be told apart from a complete
    trail, which is the difference between "this agent made 50 attempts" and
    "the first 50 of thousands".
    """
    page = container.list_tool_invocations().run(
        principal, agent_id=str(agent_id), limit=limit, offset=offset
    )
    items = [ToolInvocationAuditResponse.from_domain(item) for item in page.items]
    return ToolInvocationAuditListResponse(
        items=items, meta=PageMeta(total=page.total, limit=limit, offset=offset)
    )


@router.get(
    "/{agent_id}/tool-invocations/verification",
    response_model=ChainVerdictResponse,
    summary="Recompute an agent's audit chain and report any contradiction",
    dependencies=[RateLimitDep],
)
def verify_tool_invocation_chain(
    agent_id: UUID, principal: PrincipalDep, container: ContainerDep
) -> ChainVerdictResponse:
    """Check the audit trail instead of trusting it.

    The listing endpoint returns what the rows *say*. This recomputes each row's
    seals from its own stored fields plus its predecessor's hash, so a deleted,
    reordered or edited row surfaces as ``verified: false`` with the first
    contradiction. The whole chain is checked because a page cannot be: its first
    row links to a predecessor the page does not hold.

    Owner-only, and a non-owner gets 404 rather than 403 — the same rule as the
    listing, so this cannot be used to probe for another user's agent.
    """
    verdict = container.verify_tool_invocation_chain().run(principal, agent_id=str(agent_id))
    return ChainVerdictResponse.from_domain(verdict)


@router.get(
    "/approvals/{approval_id}",
    response_model=ApprovalRequestResponse,
    summary="Read one approval request belonging to one of the caller's agents",
    dependencies=[RateLimitDep],
)
def get_approval_request(
    approval_id: UUID, principal: PrincipalDep, container: ContainerDep
) -> ApprovalRequestResponse:
    """Read back the record for an approval the caller's agent asked for.

    The decision and revocation routes take an approval id but returned nothing
    readable afterwards, so a reviewer could not confirm what they had approved
    or withdrawn without inspecting the audit trail. A non-owner gets 404, not
    403, so an approval id cannot be used to probe for another user's agent.
    """
    request = container.get_approval_request().run(principal, approval_id=str(approval_id))
    return ApprovalRequestResponse.from_domain(request)


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


@router.get(
    "/approvals/{approval_id}/advisory",
    response_model=ApprovalAdvisoryResponse,
    summary="Ask for a provider's second opinion on a pending approval (human only)",
    dependencies=[RateLimitDep],
)
def recommend_approval_decision(
    approval_id: UUID,
    principal: PrincipalDep,
    container: ContainerDep,
) -> ApprovalAdvisoryResponse:
    """Return advice about a pending approval. Never decides it.

    This is the only production route into the judgment path. The answer is an
    advisory score plus the confidence and context digest behind it; the request
    stays ``PENDING`` and the human still has to decide. A non-owner gets 404 for
    the same reason as the rest of the approvals endpoints; a request that is
    already decided, revoked or expired gets 409, because there is no longer a
    decision for the advice to inform.
    """
    advisory = container.recommend_approval_decision().run(principal, approval_id=str(approval_id))
    return ApprovalAdvisoryResponse.from_domain(advisory)


@router.post(
    "/approvals/{approval_id}/revoke",
    response_model=ApprovalRequestResponse,
    summary="Withdraw an approved request before it is spent (human only)",
    dependencies=[RateLimitDep],
)
def revoke_approval_request(
    approval_id: UUID,
    principal: PrincipalDep,
    container: ContainerDep,
    payload: ApprovalRevokeRequest | None = None,
) -> ApprovalRequestResponse:
    """Cancel a grant that has not yet been used.

    Distinct from the decision endpoint: that one answers a *pending* request,
    this one takes back an answer already given. A request that is still pending
    is refused here (deny it instead) and a spent one is refused too, so this
    cannot be used to rewrite an authorisation that already happened.

    Owner-only. A non-owner gets 404, not 403, for the same reason as the rest of
    the approvals endpoints.
    """
    request = container.revoke_approval_request().run(
        principal,
        approval_id=str(approval_id),
        reason=payload.reason if payload else None,
    )
    return ApprovalRequestResponse.from_domain(request)
