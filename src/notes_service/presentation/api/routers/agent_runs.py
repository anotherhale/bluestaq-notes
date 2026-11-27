"""Durable agent run endpoints under ``/api/v1/agent-runs``.

This is the HTTP surface of the durable runtime. Like the tool gateway, it is a
thin edge: the use cases own every decision, so this module cannot become a
second, weaker path to a tool.

Two authorities meet here, and which one applies is a security boundary:

* **Planning and advancing are the agent's.** They require the agent token in
  ``Authorization`` and read nothing else, so a human demo identity cannot drive
  an agent's run. Planning is additionally scoped to the token's capabilities.
* **Reading, resolving, and aborting are the owner's.** An interrupted step means
  the runtime cannot tell whether a side effect landed; only the human who owns
  the agent may rule on it. That is why ``resolve`` takes a human principal and
  why an agent has no endpoint that settles its own ambiguous step.

The prefix is ``/agent-runs`` rather than a child of ``/agents`` on purpose:
``GET /agents/{agent_id}`` is typed as a UUID, so a sibling literal segment would
be shadowed by it and answer 422 instead of reaching the run handler.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from notes_service.domain.agent.runs import PlannedStep
from notes_service.presentation.dependencies import (
    AgentRateLimitDep,
    AgentTokenSecretDep,
    ContainerDep,
    PrincipalDep,
    RateLimitDep,
)
from notes_service.presentation.schemas import (
    AbortAgentRunRequest,
    AdvanceAgentRunRequest,
    AgentRunListResponse,
    AgentRunResponse,
    PageMeta,
    PlanAgentRunRequest,
    ResolveInterruptedStepRequest,
    RunAdvanceResponse,
)

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


def _page(items: list[AgentRunResponse]) -> PageMeta:
    """Listings are unpaginated, so the meta describes the full set."""
    return PageMeta(total=len(items), limit=len(items), offset=0)


def _planned_steps(payload: PlanAgentRunRequest) -> list[PlannedStep]:
    """Map requested steps onto planned steps.

    The capability is not read from the body: ``PlannedStep`` derives it from the
    tool catalog, so a caller cannot present a plan that claims less authority
    than the steps it schedules.
    """
    return [
        PlannedStep(
            tool_name=step.tool,
            arguments=step.arguments,
            resource_ref=step.resource_ref,
        )
        for step in payload.steps
    ]


@router.post(
    "",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Plan a durable run with an agent token",
    dependencies=[AgentRateLimitDep],
)
def plan_run(
    payload: PlanAgentRunRequest,
    secret: AgentTokenSecretDep,
    container: ContainerDep,
) -> AgentRunResponse:
    run = container.plan_agent_run().run(secret=secret, steps=_planned_steps(payload))
    return AgentRunResponse.from_domain(run)


@router.post(
    "/{run_id}/advance",
    response_model=RunAdvanceResponse,
    summary="Drive a durable run forward with an agent token",
    dependencies=[AgentRateLimitDep],
)
def advance_run(
    run_id: UUID,
    payload: AdvanceAgentRunRequest,
    secret: AgentTokenSecretDep,
    container: ContainerDep,
) -> RunAdvanceResponse:
    advance = container.advance_agent_run().run(
        secret=secret, run_id=str(run_id), max_steps=payload.max_steps
    )
    return RunAdvanceResponse.from_domain(advance)


@router.get(
    "",
    response_model=AgentRunListResponse,
    summary="List runs for one of the caller's agents",
    dependencies=[RateLimitDep],
)
def list_runs(
    agent_id: UUID,
    principal: PrincipalDep,
    container: ContainerDep,
    resumable_only: bool = False,
) -> AgentRunListResponse:
    runs = container.list_agent_runs().run(
        principal, agent_id=str(agent_id), resumable_only=resumable_only
    )
    items = [AgentRunResponse.from_domain(run) for run in runs]
    return AgentRunListResponse(items=items, meta=_page(items))


@router.get(
    "/{run_id}",
    response_model=AgentRunResponse,
    summary="Fetch one of the caller's runs",
    dependencies=[RateLimitDep],
)
def get_run(run_id: UUID, principal: PrincipalDep, container: ContainerDep) -> AgentRunResponse:
    run = container.get_agent_run().run(principal, run_id=str(run_id))
    return AgentRunResponse.from_domain(run)


@router.post(
    "/{run_id}/resolve",
    response_model=AgentRunResponse,
    summary="Rule on an interrupted step the caller owns",
    dependencies=[RateLimitDep],
)
def resolve_interrupted_step(
    run_id: UUID,
    payload: ResolveInterruptedStepRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> AgentRunResponse:
    run = container.resolve_interrupted_step().run(
        principal,
        run_id=str(run_id),
        effect_landed=payload.effect_landed,
        outcome=payload.outcome,
    )
    return AgentRunResponse.from_domain(run)


@router.post(
    "/{run_id}/abort",
    response_model=AgentRunResponse,
    summary="Abort one of the caller's runs permanently",
    dependencies=[RateLimitDep],
)
def abort_run(
    run_id: UUID,
    payload: AbortAgentRunRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> AgentRunResponse:
    run = container.abort_agent_run().run(principal, run_id=str(run_id), reason=payload.reason)
    return AgentRunResponse.from_domain(run)
