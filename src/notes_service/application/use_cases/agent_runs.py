"""Use cases for durable agent runs.

:class:`~notes_service.application.use_cases.tools.InvokeTool` is a single
governed call; these use cases are the durable *sequence* around it. A run is
created from a plan, executed one step at a time through the gateway, and — if
the process dies mid-step — resumed without re-applying a step that already
succeeded.

Two boundaries are deliberate:

* **Execution still goes through the gateway.** A run does not get a private path
  to a tool. Every step re-resolves the token, re-checks the capability, and
  honours approval gating, so a durable run cannot outlive the authority that
  started it. Revoking the token stops the run on its next step.
* **Only the owning human decides an ambiguous step.** A step left ``started`` by
  a crash is settled by :class:`ResolveInterruptedStep`, which requires the agent's
  owner. An agent cannot declare its own side effect landed — that would let it
  approve its own sensitive action, which the platform forbids.
"""

from __future__ import annotations

from dataclasses import dataclass

from notes_service.application.errors import (
    AccessDenied,
    ApplicationError,
    InvalidInput,
    ResourceNotFound,
)
from notes_service.application.ports.authorization import AuthorizationProvider
from notes_service.application.ports.clock import Clock
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.application.use_cases.agents import ResolveAgentToken, ResolvedToken
from notes_service.application.use_cases.base import UnitOfWorkFactory, UseCase
from notes_service.application.use_cases.tools import InvokeTool, ToolResult
from notes_service.domain.agent.runs import AgentRun, AgentRunId, PlannedStep
from notes_service.domain.agent.value_objects import AgentId, TokenAudience
from notes_service.domain.errors import DomainError, InvariantViolation
from notes_service.domain.identity.principal import Principal


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """What one executed step produced, kept out of the run's persisted state."""

    sequence: int
    tool_name: str
    output: dict[str, object]


@dataclass(frozen=True, slots=True)
class RunAdvance:
    """The result of driving a run forward as far as it can go.

    ``halted_reason`` is set when the run stopped without completing, so a caller
    can distinguish "finished" from "waiting on a human" from "a step failed".
    """

    run: AgentRun
    outcomes: list[StepOutcome]
    halted_reason: str | None = None


class _RunUseCase(UseCase):
    """Shared loading and ownership rules for run use cases."""

    def _load_owned_run(
        self, uow: UnitOfWork, *, principal: Principal, run_id: str
    ) -> tuple[AgentRun, object]:
        """Load a run the caller owns, else 404.

        Ownership is checked through the run's agent rather than stored twice on
        the run: a run inherits exactly its agent's owner, so there is one place
        where ownership can drift, and there isn't.
        """
        try:
            parsed = AgentRunId.parse(run_id)
        except DomainError as exc:
            raise ResourceNotFound("run not found") from exc
        run = uow.agent_runs.get(parsed)
        if run is None:
            raise ResourceNotFound("run not found")
        agent = uow.agents.get(AgentId.parse(run.agent_id))
        if agent is None or agent.owner_user_id != principal.user_id:
            raise ResourceNotFound("run not found")
        return run, agent


class PlanAgentRun(_RunUseCase):
    """Create a durable run from a plan, authenticated by the agent's own token.

    The agent is taken from the token, never from the request, and every planned
    step's capability must be inside the token's set. A plan that asks for
    something the token cannot do is rejected before anything is persisted, so an
    agent cannot queue authority it does not have and wait for a later resume to
    grant it.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        authorization: AuthorizationProvider,
        clock: Clock,
        resolve_token: ResolveAgentToken,
    ) -> None:
        super().__init__(uow_factory=uow_factory, authorization=authorization, clock=clock)
        self._resolve_token = resolve_token

    def run(self, *, secret: str, steps: list[PlannedStep]) -> AgentRun:
        resolved = self._resolve_token.run(secret=secret, audience=TokenAudience.TOOL_GATEWAY.value)
        if not steps:
            raise InvalidInput("a run needs at least one step")
        for step in steps:
            if step.capability not in resolved.token.capabilities:
                raise AccessDenied(
                    f"token lacks capability {step.capability.value} required by step "
                    f"{step.tool_name}"
                )
        run = AgentRun.plan(
            agent_id=resolved.agent.id,
            planned=steps,
            delegation_id=resolved.delegation.id,
        )
        with self._uow_factory() as uow:
            uow.agent_runs.add(run)
            uow.commit()
        return run


class AdvanceAgentRun(_RunUseCase):
    """Drive a run forward through the governed gateway, one step at a time.

    Stops at the first step that cannot complete. An interrupted step halts the
    run rather than being retried, because only a human can say whether its
    effect landed.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        authorization: AuthorizationProvider,
        clock: Clock,
        resolve_token: ResolveAgentToken,
        gateway: InvokeTool,
    ) -> None:
        super().__init__(uow_factory=uow_factory, authorization=authorization, clock=clock)
        self._resolve_token = resolve_token
        self._gateway = gateway

    def run(self, *, secret: str, run_id: str, max_steps: int = 100) -> RunAdvance:
        resolved = self._resolve_token.run(secret=secret, audience=TokenAudience.TOOL_GATEWAY.value)
        run = self._load_for_agent(resolved, run_id)

        if run.status.is_terminal:
            return RunAdvance(run=run, outcomes=[], halted_reason=f"run is {run.status.value}")

        run.mark_interrupted()
        if run.interrupted_step is not None:
            self._save(run)
            return RunAdvance(
                run=run,
                outcomes=[],
                halted_reason=(
                    f"step {run.interrupted_step.sequence} was interrupted; "
                    "an owner must resolve whether its effect landed"
                ),
            )

        run.resume()
        outcomes: list[StepOutcome] = []
        halted: str | None = None
        executed = 0

        while executed < max_steps:
            step = run.next_step
            if step is None:
                halted = None if run.is_complete else "an earlier step is unfinished"
                break
            run.begin_step(step.id)
            self._save(run)
            try:
                result = self._gateway.run(
                    secret=secret,
                    tool_name=step.tool_name,
                    arguments=step.arguments,
                    idempotency_key=step.idempotency_key,
                )
            except ApplicationError as exc:
                run.fail_step(step.id, outcome=f"{exc.code}: {exc}")
                self._save(run)
                return RunAdvance(run=run, outcomes=outcomes, halted_reason=str(exc))
            except DomainError as exc:
                run.fail_step(step.id, outcome=type(exc).__name__)
                self._save(run)
                return RunAdvance(run=run, outcomes=outcomes, halted_reason=str(exc))
            run.complete_step(step.id, outcome=self._step_outcome(result))
            self._save(run)
            outcomes.append(
                StepOutcome(sequence=step.sequence, tool_name=step.tool_name, output=result.output)
            )
            executed += 1

        if executed >= max_steps and not run.is_complete:
            halted = f"stopped after {max_steps} steps"
        return RunAdvance(run=run, outcomes=outcomes, halted_reason=halted)

    def _load_for_agent(self, resolved: ResolvedToken, run_id: str) -> AgentRun:
        try:
            parsed = AgentRunId.parse(run_id)
        except DomainError as exc:
            raise ResourceNotFound("run not found") from exc
        with self._uow_factory() as uow:
            run = uow.agent_runs.get(parsed)
        if run is None or AgentId.parse(run.agent_id) != resolved.agent.id:
            raise ResourceNotFound("run not found")
        return run

    def _save(self, run: AgentRun) -> None:
        with self._uow_factory() as uow:
            uow.agent_runs.save(run)
            uow.commit()

    @staticmethod
    def _step_outcome(result: ToolResult) -> str:
        return "ok (replayed)" if result.replayed else "ok"


class ResolveInterruptedStep(_RunUseCase):
    """Settle an interrupted step from an explicit owner decision.

    The owner states whether the step's effect landed. The domain does not infer
    it: retrying an effect that landed duplicates it, and skipping one that did
    not loses it. Both are worse than asking.
    """

    def run(
        self,
        principal: Principal,
        *,
        run_id: str,
        effect_landed: bool,
        outcome: str | None = None,
    ) -> AgentRun:
        with self._uow_factory() as uow:
            run, _agent = self._load_owned_run(uow, principal=principal, run_id=run_id)
            try:
                run.resolve_interrupted(effect_landed=effect_landed, outcome=outcome)
            except InvariantViolation as exc:
                raise InvalidInput(str(exc)) from exc
            uow.agent_runs.save(run)
            uow.commit()
        return run


class AbortAgentRun(_RunUseCase):
    """Stop a run permanently. The owner's decision, recorded with its reason."""

    def run(self, principal: Principal, *, run_id: str, reason: str | None = None) -> AgentRun:
        with self._uow_factory() as uow:
            run, _agent = self._load_owned_run(uow, principal=principal, run_id=run_id)
            try:
                run.abort(reason=reason)
            except InvariantViolation as exc:
                raise InvalidInput(str(exc)) from exc
            uow.agent_runs.save(run)
            uow.commit()
        return run


class GetAgentRun(_RunUseCase):
    """Read one run the caller owns. Unreadable and missing are both 404."""

    def run(self, principal: Principal, *, run_id: str) -> AgentRun:
        with self._uow_factory() as uow:
            run, _agent = self._load_owned_run(uow, principal=principal, run_id=run_id)
        return run


class ListAgentRuns(_RunUseCase):
    """List runs for one agent the caller owns, newest first."""

    def run(
        self, principal: Principal, *, agent_id: str, resumable_only: bool = False
    ) -> list[AgentRun]:
        with self._uow_factory() as uow:
            agent = uow.agents.get(AgentId.parse(agent_id))
            if agent is None or agent.owner_user_id != principal.user_id:
                raise ResourceNotFound("agent not found")
            if resumable_only:
                return uow.agent_runs.list_resumable_for_agent(agent.id)
            return uow.agent_runs.list_for_agent(agent.id)


__all__ = [
    "AbortAgentRun",
    "AdvanceAgentRun",
    "GetAgentRun",
    "ListAgentRuns",
    "PlanAgentRun",
    "ResolveInterruptedStep",
    "RunAdvance",
    "StepOutcome",
]
