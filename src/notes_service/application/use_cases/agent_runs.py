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
    ApprovalRequired,
    InvalidInput,
    ResourceNotFound,
)
from notes_service.application.ports.authorization import AuthorizationProvider
from notes_service.application.ports.clock import Clock
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.application.use_cases.agents import ResolveAgentToken, ResolvedToken
from notes_service.application.use_cases.base import UnitOfWorkFactory, UseCase
from notes_service.application.use_cases.tools import InvokeTool, ToolResult
from notes_service.domain.agent.runs import (
    AgentRun,
    AgentRunId,
    PlannedStep,
    StepStatus,
    step_idempotency_key,
)
from notes_service.domain.agent.tools import (
    InvocationStatus,
    ToolInvocation,
    resolve_tool,
    validate_arguments,
)
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

    The capability recorded for a step is the one the **tool catalog** declares,
    not the one the caller claims: ``PlannedStep`` derives it from the catalog on
    construction, so a plan naming ``delete_note`` can never persist a step whose
    recorded authority is weaker than what it will actually do. The run row is what
    an operator reads when deciding an interrupted step, so that recorded authority
    has to be the real one.
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
        checked = [self._checked_step(step) for step in steps]
        for step in checked:
            if step.capability not in resolved.token.capabilities:
                raise AccessDenied(
                    f"token lacks capability {step.capability.value} required by step "
                    f"{step.tool_name}"
                )
        run = AgentRun.plan(
            agent_id=resolved.agent.id,
            planned=checked,
            delegation_id=resolved.delegation.id,
        )
        with self._uow_factory() as uow:
            uow.agent_runs.add(run)
            uow.commit()
        return run

    @staticmethod
    def _checked_step(step: PlannedStep) -> PlannedStep:
        """Validate the step's arguments against the tool catalog.

        ``PlannedStep`` already derives and records the catalog capability, so only
        the arguments need checking here — a step whose arguments do not fit its
        tool would fail mid-run instead of at plan time.
        """
        try:
            tool = resolve_tool(step.tool_name)
            arguments = validate_arguments(tool, step.arguments)
        except DomainError as exc:
            raise InvalidInput(str(exc)) from exc
        return PlannedStep(
            tool_name=tool.name,
            arguments=arguments,
            resource_ref=step.resource_ref,
        )


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
            except ApprovalRequired as exc:
                # Waiting on a human is not a decision, so the step is parked and
                # the run stays open. Failing it here would make the run terminal
                # and strand the approval a human may still grant.
                run.defer_step(step.id)
                self._save(run)
                return RunAdvance(
                    run=run,
                    outcomes=outcomes,
                    halted_reason=f"awaiting human approval: {exc}",
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
        # A run is bound to the delegation that planned it, not merely to its
        # agent. Checking the agent alone lets a token minted from a *different*
        # delegation advance a plan authorised by one whose consent may since
        # have been withdrawn — the run would then outlive the authority that
        # started it, which is exactly what re-resolving the token per step is
        # meant to prevent. A second token from the same delegation is still
        # accepted: that is the same consent, not a new one. The column is
        # nullable, so a run with no recorded binding fails closed rather than
        # being advanced on an unrecorded authority.
        if run.delegation_id is None or run.delegation_id != resolved.delegation.id:
            raise AccessDenied("run is bound to a delegation other than this token's")
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

    The ruling also seals the gateway's audit record for that step. The gateway
    writes its invocation ``started`` *before* entering the handler, so a crash
    leaves the link permanently open — and an open link is indistinguishable
    from an attempt still in flight. Settling only the run would leave the
    tamper-evident trail asserting "outcome unknown" about an effect a human has
    since ruled on, which is the one thing the trail exists to prevent. Both
    records are settled in one transaction so they cannot disagree.
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
            step = run.interrupted_step
            if step is None:
                raise InvalidInput(f"run {run.id} has no interrupted step to resolve")
            sequence = step.sequence
            try:
                run.resolve_interrupted(effect_landed=effect_landed, outcome=outcome)
            except InvariantViolation as exc:
                raise InvalidInput(str(exc)) from exc
            uow.agent_runs.save(run)
            self._seal_step_audit(
                uow, run=run, sequence=sequence, effect_landed=effect_landed, outcome=outcome
            )
            uow.commit()
        return run

    def _seal_step_audit(
        self,
        uow: UnitOfWork,
        *,
        run: AgentRun,
        sequence: int,
        effect_landed: bool,
        outcome: str | None,
    ) -> None:
        """Close the invocation the interrupted step left open, if it is open.

        A step that crashed *before* the gateway wrote its row has nothing to
        seal, and a step whose invocation already reached a terminal status is
        left alone: the audit record's own outcome is the stronger evidence, and
        rewriting a settled link would defeat the chain's purpose.
        """
        invocation = uow.tool_invocations.find_by_idempotency_key(
            AgentId.parse(run.agent_id), step_idempotency_key(run.id, sequence)
        )
        if invocation is None or invocation.status is not InvocationStatus.STARTED:
            return
        if effect_landed:
            invocation.succeed(outcome=outcome or "effect confirmed after interruption")
        else:
            invocation.fail(outcome=outcome or "owner ruled the effect did not land")
        uow.tool_invocations.save(invocation)


class AbortAgentRun(_RunUseCase):
    """Stop a run permanently. The owner's decision, recorded with its reason.

    Aborting is a way of *settling* a run, so it carries the same obligation as
    :class:`ResolveInterruptedStep`: no record describing the run may be left
    mid-flight. A run whose step was interrupted has a gateway invocation still
    ``started``, and leaving it open beside an ``aborted`` run would keep the
    tamper-evident trail asserting the outcome is unknown — the same defect NEX-023
    closed for the resolve path. The link is settled as *not confirmed*, because
    the owner stopped the run without ruling on the effect, and both records are
    written in one transaction so they cannot disagree.
    """

    def run(self, principal: Principal, *, run_id: str, reason: str | None = None) -> AgentRun:
        with self._uow_factory() as uow:
            run, _agent = self._load_owned_run(uow, principal=principal, run_id=run_id)
            mid_flight = [
                step.sequence
                for step in run.ordered_steps
                if step.status in {StepStatus.STARTED, StepStatus.INTERRUPTED}
            ]
            try:
                run.abort(reason=reason)
            except InvariantViolation as exc:
                raise InvalidInput(str(exc)) from exc
            uow.agent_runs.save(run)
            for sequence in mid_flight:
                self._seal_abandoned_step(uow, run=run, sequence=sequence)
            uow.commit()
        return run

    def _seal_abandoned_step(self, uow: UnitOfWork, *, run: AgentRun, sequence: int) -> None:
        invocation: ToolInvocation | None = uow.tool_invocations.find_by_idempotency_key(
            AgentId.parse(run.agent_id), step_idempotency_key(run.id, sequence)
        )
        if invocation is None or invocation.status is not InvocationStatus.STARTED:
            return
        invocation.fail(outcome="run aborted before this step's effect was confirmed")
        uow.tool_invocations.save(invocation)


class GetAgentRun(_RunUseCase):
    """Read one run the caller owns. Unreadable and missing are both 404."""

    def run(self, principal: Principal, *, run_id: str) -> AgentRun:
        with self._uow_factory() as uow:
            run, _agent = self._load_owned_run(uow, principal=principal, run_id=run_id)
        return run


@dataclass(frozen=True, slots=True)
class AgentRunPage:
    """One page of an agent's run history plus the history's exact size.

    ``total`` counts the whole set, not the page. The listing is capped, so a
    page-derived total would equal the page size on every page and make a window
    over 1,208 runs indistinguishable from a complete history of 50 — the
    difference between "this agent ran 50 times" and "the first 50 of many".
    """

    items: list[AgentRun]
    total: int


class ListAgentRuns(_RunUseCase):
    """List runs for one agent the caller owns, newest first.

    Bounded and totalled (NEX-090)
    ------------------------------

    The store's listing was capped at 50 rows while the route reported
    ``total=len(items)``, so an agent with more history than one page looked
    like it had exactly one page of it, and there was no way to reach the rest:
    the endpoint took no ``limit``/``offset`` at all. The window is now the
    caller's, ``meta.total`` is a ``COUNT`` over the same agent-scoped set, and
    both statements run in one transaction so a page and its total describe the
    same moment.

    ``resumable_only`` is deliberately *not* windowed. It is the recovery set —
    a caller resuming interrupted work must see every run that needs resuming,
    so truncating it would silently abandon runs. Its total is the exact size of
    that set, which is the whole set.
    """

    def run(
        self,
        principal: Principal,
        *,
        agent_id: str,
        resumable_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> AgentRunPage:
        with self._uow_factory() as uow:
            agent = uow.agents.get(AgentId.parse(agent_id))
            if agent is None or agent.owner_user_id != principal.user_id:
                raise ResourceNotFound("agent not found")
            if resumable_only:
                resumable = uow.agent_runs.list_resumable_for_agent(agent.id)
                return AgentRunPage(items=resumable, total=len(resumable))
            items = uow.agent_runs.list_for_agent(agent.id, limit=limit, offset=offset)
            total = uow.agent_runs.count_for_agent(agent.id)
            return AgentRunPage(items=items, total=total)


__all__ = [
    "AbortAgentRun",
    "AdvanceAgentRun",
    "AgentRunPage",
    "GetAgentRun",
    "ListAgentRuns",
    "PlanAgentRun",
    "ResolveInterruptedStep",
    "RunAdvance",
    "StepOutcome",
]
