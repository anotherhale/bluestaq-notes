"""Tests for durable agent runs (NEX-009).

The run is the durable *sequence* around the governed tool gateway. What matters
here is not that steps run, but that they survive a crash without duplicating or
losing an effect. Four invariants are pinned directly:

* **Execution stays inside the gateway.** A run cannot reach a tool its token
  lacks, and a plan naming a capability the token does not hold is refused before
  it is persisted — an agent cannot queue authority and wait for a later resume.
* **An interrupted step is never guessed at.** A crash leaves the step
  ``interrupted``; only the owning human can say whether the effect landed, and
  until they do the run halts instead of retrying.
* **A resume does not re-run settled steps.** Steps that already succeeded are
  skipped, so resuming is idempotent at the run level as well as the call level.
* **Runs are owner-scoped.** Another user's run is 404, not 403, matching the
  platform's rule for unreadable resources.

Application-level, like ``test_tool_gateway.py``: these assert use-case behaviour
over the real unit of work, not HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import AccessDenied, InvalidInput, ResourceNotFound
from notes_service.application.use_cases.agent_runs import (
    AbortAgentRun,
    AdvanceAgentRun,
    GetAgentRun,
    ListAgentRuns,
    PlanAgentRun,
    ResolveInterruptedStep,
)
from notes_service.application.use_cases.agents import (
    GrantDelegation,
    IssueAgentToken,
    RegisterAgent,
    ResolveAgentToken,
    RevokeDelegation,
)
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.notes import ListNotes
from notes_service.application.use_cases.tools import InvokeTool
from notes_service.domain.agent.runs import AgentRunId, PlannedStep, RunStatus, StepStatus
from notes_service.domain.agent.value_objects import Capability, TokenAudience
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.security.clock import SystemClock

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
BOB = UserId(DEMO_USERS[1].id)
TOOL_GATEWAY = TokenAudience.TOOL_GATEWAY.value


def principal_for(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email(f"{user_id}@example.com"),
        display_name="Caller",
        provider="demo",
        subject=str(user_id),
    )


def create_step(title: str) -> PlannedStep:
    return PlannedStep(
        tool_name="create_note",
        capability=Capability.CREATE_NOTE,
        arguments={"title": title, "content": f"body for {title}"},
    )


@dataclass
class Harness:
    session_factory: sessionmaker[Session]
    principal: Principal
    plan: PlanAgentRun
    advance: AdvanceAgentRun
    resolve: ResolveInterruptedStep
    abort: AbortAgentRun
    get: GetAgentRun
    list_runs: ListAgentRuns
    register: RegisterAgent
    delegate: GrantDelegation
    issue: IssueAgentToken
    revoke_delegation: RevokeDelegation

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.session_factory)

    def agent(self, *capabilities: Capability):
        return self.register.run(
            self.principal,
            display_name="Planner",
            runtime_identity="svc-planner",
            capabilities=[c.value for c in capabilities],
        )

    def secret_for(self, *capabilities: Capability) -> tuple[object, str]:
        caps = capabilities or (Capability.CREATE_NOTE,)
        agent = self.agent(*caps)
        delegation = self.delegate.run(
            self.principal, agent_id=str(agent.id), capabilities=[c.value for c in caps]
        )
        secret = self.issue.run(
            self.principal,
            delegation_id=str(delegation.id),
            capabilities=[c.value for c in caps],
            audience=TOOL_GATEWAY,
            ttl_seconds=300,
        ).secret
        return agent, secret

    def run_titles(self, agent) -> list[str]:
        """Titles of the notes the delegator can see, proving real effects landed."""
        listed = ListNotes(
            uow_factory=lambda: self.uow(),
            authorization=DefaultAuthorizationProvider(),
            clock=SystemClock(),
        ).run(self.principal)
        return sorted(note.title for note in listed.items)


@pytest.fixture
def h(session_factory: sessionmaker[Session]) -> Harness:
    auth = DefaultAuthorizationProvider()
    clock = SystemClock()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    def make(cls, **extra):  # type: ignore[no-untyped-def]
        return cls(uow_factory=uow_factory, authorization=auth, clock=clock, **extra)

    resolve_token = make(ResolveAgentToken)
    gateway = make(InvokeTool, resolve_token=resolve_token)
    return Harness(
        session_factory=session_factory,
        principal=principal_for(ALICE),
        plan=make(PlanAgentRun, resolve_token=resolve_token),
        advance=make(AdvanceAgentRun, resolve_token=resolve_token, gateway=gateway),
        resolve=make(ResolveInterruptedStep),
        abort=make(AbortAgentRun),
        get=make(GetAgentRun),
        list_runs=make(ListAgentRuns),
        register=make(RegisterAgent),
        delegate=make(GrantDelegation),
        issue=make(IssueAgentToken),
        revoke_delegation=make(RevokeDelegation),
    )


def test_run_executes_every_step_in_order_and_completes(h: Harness) -> None:
    _agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("first"), create_step("second")])

    advance = h.advance.run(secret=secret, run_id=str(run.id))

    assert advance.halted_reason is None
    assert advance.run.status is RunStatus.COMPLETED
    assert [s.sequence for s in advance.run.ordered_steps] == [1, 2]
    assert all(s.status is StepStatus.SUCCEEDED for s in advance.run.ordered_steps)
    assert [o.tool_name for o in advance.outcomes] == ["create_note", "create_note"]
    assert h.run_titles(_agent) == ["first", "second"]


def test_plan_refuses_a_capability_the_token_lacks(h: Harness) -> None:
    """A plan is authority-scoped up front, not discovered step by step."""
    _agent, secret = h.secret_for(Capability.CREATE_NOTE)

    with pytest.raises(AccessDenied):
        h.plan.run(
            secret=secret,
            steps=[
                create_step("allowed"),
                PlannedStep(
                    tool_name="delete_note",
                    capability=Capability.DELETE_NOTE,
                    arguments={"note_id": "00000000-0000-0000-0000-000000000000"},
                ),
            ],
        )

    with h.uow() as uow:
        assert uow.agent_runs.list_for_agent(AgentRunId.parse(str(_agent.id))) == []


def test_empty_plan_is_rejected(h: Harness) -> None:
    _agent, secret = h.secret_for()
    with pytest.raises(InvalidInput):
        h.plan.run(secret=secret, steps=[])


def test_a_crashed_step_halts_the_run_until_an_owner_decides(h: Harness) -> None:
    """The core durability guarantee: no guessing about an interrupted effect."""
    agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("one"), create_step("two")])

    # Simulate a crash mid-step: the step is persisted ``started`` and never settles.
    run.resume()
    run.begin_step(run.ordered_steps[0].id)
    with h.uow() as uow:
        uow.agent_runs.save(run)
        uow.commit()

    resumed = h.advance.run(secret=secret, run_id=str(run.id))

    assert resumed.run.status is RunStatus.INTERRUPTED
    assert "interrupted" in (resumed.halted_reason or "")
    assert resumed.outcomes == []
    assert resumed.run.ordered_steps[0].status is StepStatus.INTERRUPTED

    # An agent cannot settle its own interrupted step; the run is not advanceable.
    still_halted = h.advance.run(secret=secret, run_id=str(run.id))
    assert still_halted.run.status is RunStatus.INTERRUPTED

    # The owner confirms the effect landed. The step is NOT executed again.
    resolved = h.resolve.run(h.principal, run_id=str(run.id), effect_landed=True)
    assert resolved.ordered_steps[0].status is StepStatus.SUCCEEDED
    assert h.run_titles(agent) == []

    finished = h.advance.run(secret=secret, run_id=str(run.id))
    assert finished.run.status is RunStatus.COMPLETED
    assert [o.sequence for o in finished.outcomes] == [2]
    assert h.run_titles(agent) == ["two"]


def test_owner_can_declare_an_interrupted_effect_did_not_land(h: Harness) -> None:
    _agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("only")])
    run.resume()
    run.begin_step(run.ordered_steps[0].id)
    run.mark_interrupted()
    with h.uow() as uow:
        uow.agent_runs.save(run)
        uow.commit()

    resolved = h.resolve.run(h.principal, run_id=str(run.id), effect_landed=False)

    assert resolved.status is RunStatus.FAILED
    assert resolved.ordered_steps[0].status is StepStatus.FAILED


def test_resolving_a_run_with_no_interrupted_step_is_rejected(h: Harness) -> None:
    _agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("only")])

    with pytest.raises(InvalidInput):
        h.resolve.run(h.principal, run_id=str(run.id), effect_landed=True)


def test_a_revoked_delegation_stops_the_run(h: Harness) -> None:
    """Durability must not outlive authority: revocation applies on the next step."""
    agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("one"), create_step("two")])

    with h.uow() as uow:
        delegation_id = uow.agent_runs.get(run.id).delegation_id  # type: ignore[union-attr]
    h.revoke_delegation.run(h.principal, delegation_id=str(delegation_id))

    with pytest.raises(AccessDenied, match="revoked"):
        h.advance.run(secret=secret, run_id=str(run.id))
    assert h.run_titles(agent) == []


def test_another_user_cannot_read_or_abort_a_run(h: Harness) -> None:
    _agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("private")])
    intruder = principal_for(BOB)

    with pytest.raises(ResourceNotFound):
        h.get.run(intruder, run_id=str(run.id))
    with pytest.raises(ResourceNotFound):
        h.abort.run(intruder, run_id=str(run.id), reason="not mine")
    with pytest.raises(ResourceNotFound):
        h.list_runs.run(intruder, agent_id=str(_agent.id))

    assert h.get.run(h.principal, run_id=str(run.id)).status is RunStatus.PENDING


def test_abort_is_terminal_and_cannot_be_resumed(h: Harness) -> None:
    _agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("one")])

    aborted = h.abort.run(h.principal, run_id=str(run.id), reason="operator stopped it")

    assert aborted.status is RunStatus.ABORTED
    assert aborted.last_error == "operator stopped it"
    advance = h.advance.run(secret=secret, run_id=str(run.id))
    assert advance.outcomes == []
    assert "aborted" in (advance.halted_reason or "")
    with pytest.raises(InvalidInput):
        h.abort.run(h.principal, run_id=str(run.id))


def test_resumable_list_excludes_settled_runs(h: Harness) -> None:
    agent, secret = h.secret_for()
    done = h.plan.run(secret=secret, steps=[create_step("done")])
    h.advance.run(secret=secret, run_id=str(done.id))
    pending = h.plan.run(secret=secret, steps=[create_step("later")])

    resumable = h.list_runs.run(h.principal, agent_id=str(agent.id), resumable_only=True)

    assert [r.id for r in resumable] == [pending.id]
    assert len(h.list_runs.run(h.principal, agent_id=str(agent.id))) == 2


def test_a_step_cannot_be_skipped_by_the_domain(h: Harness) -> None:
    _agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("one"), create_step("two")])
    run.resume()

    from notes_service.domain.errors import InvariantViolation

    with pytest.raises(InvariantViolation):
        run.begin_step(run.ordered_steps[1].id)


def test_missing_run_is_404(h: Harness) -> None:
    with pytest.raises(ResourceNotFound):
        h.get.run(h.principal, run_id="00000000-0000-0000-0000-000000000000")
    with pytest.raises(ResourceNotFound):
        h.get.run(h.principal, run_id="not-a-uuid")
