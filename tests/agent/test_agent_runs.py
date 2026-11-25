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
from notes_service.domain.agent.audit_chain import verify_chain
from notes_service.domain.agent.runs import (
    AgentRunId,
    PlannedStep,
    RunStatus,
    StepStatus,
    step_idempotency_key,
)
from notes_service.domain.agent.tools import InvocationStatus, ToolInvocation, resolve_tool
from notes_service.domain.agent.value_objects import AgentId, Capability, TokenAudience
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


def _crash_mid_step(h: Harness, agent, run) -> ToolInvocation:
    """Leave ``run``'s first step interrupted with an open audit link.

    This mirrors exactly what the gateway does before entering a handler: it
    commits the invocation ``started``, then the process dies. Building the row
    directly is the only way to reproduce that state without an actual crash.
    """
    run.resume()
    step = run.ordered_steps[0]
    run.begin_step(step.id)
    with h.uow() as uow:
        uow.agent_runs.save(run)
        sequence, previous = uow.tool_invocations.next_chain_link(AgentId.parse(agent.id))
        invocation = ToolInvocation.begin(
            agent_id=AgentId.parse(agent.id),
            token_id=None,
            tool=resolve_tool(step.tool_name),
            idempotency_key=step_idempotency_key(run.id, step.sequence),
            arguments=step.arguments,
            resource_ref=step.resource_ref,
            previous_hash=previous,
            chain_sequence=sequence,
        )
        uow.tool_invocations.add(invocation)
        uow.commit()
    return invocation


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


def test_owner_ruling_seals_the_audit_record_the_crash_left_open(h: Harness) -> None:
    """A settled run must not leave the tamper-evident trail claiming "unknown".

    The gateway writes its invocation ``started`` before entering a handler, so a
    crash mid-step leaves that link open forever. If resolving the run did not
    also settle the invocation, the trail would keep asserting the outcome is
    unknown about an effect a human has explicitly ruled on — and ``STARTED`` is
    precisely what an in-flight attempt looks like, so the two are
    indistinguishable afterwards.
    """
    agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("one")])
    invocation = _crash_mid_step(h, agent, run)

    with h.uow() as uow:
        stored = uow.agent_runs.get(run.id)
        assert stored is not None
        stored.mark_interrupted()
        uow.agent_runs.save(stored)
        uow.commit()
        assert (
            uow.tool_invocations.get(invocation.id).status  # type: ignore[union-attr]
            is InvocationStatus.STARTED
        )

    resolved = h.resolve.run(h.principal, run_id=str(run.id), effect_landed=True)
    assert resolved.ordered_steps[0].status is StepStatus.SUCCEEDED

    with h.uow() as uow:
        sealed = uow.tool_invocations.get(invocation.id)
        assert sealed is not None
        assert sealed.status is InvocationStatus.SUCCEEDED
        assert sealed.completed_at is not None
        assert sealed.outcome_hash is not None
        chain = uow.tool_invocations.list_chain(AgentId.parse(agent.id))
        assert all(link.status is not InvocationStatus.STARTED for link in chain)
        verdict = verify_chain(chain)
        assert verdict.verified, verdict.reason


def test_owner_ruling_records_a_failure_when_the_effect_did_not_land(h: Harness) -> None:
    """The trail records what the human actually ruled, not a blanket success."""
    agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("only")])
    invocation = _crash_mid_step(h, agent, run)

    with h.uow() as uow:
        stored = uow.agent_runs.get(run.id)
        assert stored is not None
        stored.mark_interrupted()
        uow.agent_runs.save(stored)
        uow.commit()

    h.resolve.run(h.principal, run_id=str(run.id), effect_landed=False)

    with h.uow() as uow:
        sealed = uow.tool_invocations.get(invocation.id)
        assert sealed is not None
        assert sealed.status is InvocationStatus.FAILED
        assert sealed.outcome is not None


def test_resolving_leaves_an_already_terminal_audit_link_untouched(h: Harness) -> None:
    """A link the gateway already settled is evidence; the ruling must not rewrite it."""
    agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("only")])

    # Execute the step for real, then force the run back into ``interrupted``.
    h.advance.run(secret=secret, run_id=str(run.id))
    with h.uow() as uow:
        stored = uow.agent_runs.get(run.id)
        assert stored is not None
        step = stored.ordered_steps[0]
        step.status = StepStatus.INTERRUPTED
        step.completed_at = None
        stored.status = RunStatus.INTERRUPTED
        uow.agent_runs.save(stored)
        uow.commit()

    with h.uow() as uow:
        before = uow.tool_invocations.find_by_idempotency_key(
            AgentId.parse(agent.id), step_idempotency_key(run.id, 1)
        )
        assert before is not None
        assert before.status is InvocationStatus.SUCCEEDED
        original_outcome = before.outcome
        original_hash = before.outcome_hash

    h.resolve.run(h.principal, run_id=str(run.id), effect_landed=False)

    with h.uow() as uow:
        after = uow.tool_invocations.find_by_idempotency_key(
            AgentId.parse(agent.id), step_idempotency_key(run.id, 1)
        )
        assert after is not None
        assert after.status is InvocationStatus.SUCCEEDED
        assert after.outcome == original_outcome
        assert after.outcome_hash == original_hash


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


def test_abort_seals_the_audit_link_the_crash_left_open(h: Harness) -> None:
    """Aborting settles the run, so it must settle the run's open audit link too.

    A crash leaves the gateway invocation ``started``. Aborting is the owner's
    *other* way to settle a run — the one NEX-023 did not cover — and if it left
    that link open the tamper-evident trail would keep asserting the outcome is
    unknown about a run that is definitively over, indistinguishable from a call
    still in flight. The link is recorded as failed, not succeeded: the owner
    stopped the run without ruling that the effect landed.
    """
    agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("one")])
    invocation = _crash_mid_step(h, agent, run)

    # Recover the crash the way a real resume would, leaving the step interrupted.
    h.advance.run(secret=secret, run_id=str(run.id))
    aborted = h.abort.run(h.principal, run_id=str(run.id), reason="operator stopped it")

    assert aborted.status is RunStatus.ABORTED
    assert aborted.ordered_steps[0].status is StepStatus.FAILED
    assert "aborted" in (aborted.ordered_steps[0].outcome or "")

    with h.uow() as uow:
        sealed = uow.tool_invocations.get(invocation.id)
        chain = uow.tool_invocations.list_chain(AgentId.parse(agent.id))
    assert sealed is not None
    assert sealed.status is InvocationStatus.FAILED
    assert sealed.completed_at is not None
    assert sealed.outcome_hash is not None
    assert all(link.status is not InvocationStatus.STARTED for link in chain)
    assert verify_chain(chain).verified is True


def test_abort_leaves_a_settled_step_and_its_link_untouched(h: Harness) -> None:
    """A completed step is not rewritten by an abort of the run around it."""
    agent, secret = h.secret_for()
    run = h.plan.run(secret=secret, steps=[create_step("first"), create_step("second")])
    run.resume()
    run.begin_step(run.ordered_steps[0].id)
    run.complete_step(run.ordered_steps[0].id, outcome="ok")
    with h.uow() as uow:
        uow.agent_runs.save(run)
        sequence, previous = uow.tool_invocations.next_chain_link(AgentId.parse(agent.id))
        settled = ToolInvocation.begin(
            agent_id=AgentId.parse(agent.id),
            token_id=None,
            tool=resolve_tool("create_note"),
            idempotency_key=step_idempotency_key(run.id, 1),
            arguments=run.ordered_steps[0].arguments,
            resource_ref=run.ordered_steps[0].resource_ref,
            previous_hash=previous,
            chain_sequence=sequence,
        )
        settled.succeed(outcome="ok")
        uow.tool_invocations.add(settled)
        uow.commit()

    aborted = h.abort.run(h.principal, run_id=str(run.id), reason="stop")

    assert aborted.ordered_steps[0].status is StepStatus.SUCCEEDED
    with h.uow() as uow:
        after = uow.tool_invocations.get(settled.id)
    assert after is not None
    assert after.status is InvocationStatus.SUCCEEDED
    assert after.outcome == "ok"


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


def test_a_planned_step_takes_its_capability_from_the_tool_catalog() -> None:
    """A step cannot be constructed with a caller-chosen capability at all.

    ``capability`` is not an init field, so a plan cannot present weaker authority
    than the tool it schedules — the mismatch is unrepresentable rather than
    merely rejected.
    """
    step = PlannedStep(tool_name="delete_note", arguments={"note_id": "n"})
    assert step.capability is Capability.DELETE_NOTE

    with pytest.raises(TypeError):
        PlannedStep(  # type: ignore[call-arg]
            tool_name="delete_note",
            capability=Capability.READ_NOTE,
            arguments={"note_id": "n"},
        )


def test_a_planned_step_naming_an_unknown_tool_is_rejected() -> None:
    from notes_service.domain.errors import ValidationError

    with pytest.raises(ValidationError, match="unknown tool"):
        PlannedStep(tool_name="no_such_tool", arguments={})


def test_advance_refuses_a_token_from_a_different_delegation(h: Harness) -> None:
    """A run is bound to the delegation that planned it, not just to its agent.

    Checking only the agent id let a second delegation's token drive a plan
    authorised by the first, so a run could outlive the consent that started it.
    A second token from the *same* delegation is still the same consent and must
    keep working — the refusal has to be about the delegation, not about minting
    a fresh token.
    """
    agent, secret = h.secret_for(Capability.CREATE_NOTE)
    run = h.plan.run(secret=secret, steps=[create_step("bound")])

    # A second delegation for the same agent, and a token minted from it.
    other = h.delegate.run(
        h.principal, agent_id=str(agent.id), capabilities=[Capability.CREATE_NOTE.value]
    )
    foreign_secret = h.issue.run(
        h.principal,
        delegation_id=str(other.id),
        capabilities=[Capability.CREATE_NOTE.value],
        audience=TOOL_GATEWAY,
        ttl_seconds=300,
    ).secret

    with pytest.raises(AccessDenied):
        h.advance.run(secret=foreign_secret, run_id=str(run.id))

    # The refusal left the run untouched: nothing executed.
    assert h.run_titles(agent) == []
    with h.uow() as uow:
        reloaded = uow.agent_runs.get(run.id)
    assert reloaded is not None
    assert all(s.status is StepStatus.PENDING for s in reloaded.ordered_steps)

    # The planning delegation's consent still drives it, even via a fresh token.
    same_delegation_secret = h.issue.run(
        h.principal,
        delegation_id=str(run.delegation_id),
        capabilities=[Capability.CREATE_NOTE.value],
        audience=TOOL_GATEWAY,
        ttl_seconds=300,
    ).secret
    advanced = h.advance.run(secret=same_delegation_secret, run_id=str(run.id))
    assert advanced.run.status is RunStatus.COMPLETED
    assert h.run_titles(agent) == ["bound"]


def test_advance_fails_closed_when_a_run_records_no_delegation(h: Harness) -> None:
    """An unbound run cannot be advanced on unrecorded authority.

    ``delegation_id`` is nullable and its foreign key is ``ON DELETE SET NULL``,
    so a stored run really can lose its binding. That must fail closed: an
    unbound row cannot be read as "any token for this agent will do".
    """
    from sqlalchemy import update

    from notes_service.infrastructure.db.models import AgentRunRow

    agent, secret = h.secret_for(Capability.CREATE_NOTE)
    run = h.plan.run(secret=secret, steps=[create_step("orphan")])

    # ``save`` never rewrites the binding (it is immutable), so clear it the way
    # the database would: a direct UPDATE, standing in for the SET NULL cascade.
    with h.uow() as uow:
        uow.session.execute(
            update(AgentRunRow).where(AgentRunRow.id == run.id).values(delegation_id=None)
        )
        uow.commit()

    with pytest.raises(AccessDenied):
        h.advance.run(secret=secret, run_id=str(run.id))
    assert h.run_titles(agent) == []
