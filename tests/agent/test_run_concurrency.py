"""Concurrent writers must not silently overwrite one another's run progress (NEX-019).

`AgentRun.version` exists and is bumped on every aggregate mutation, but the
persistence adapter rewrote `agent_runs.version` unconditionally. Two writers
that loaded the same run therefore both succeeded, and the second commit erased
the first one's step progress — a lost update, with no conflict surfaced to
either caller.

That matters more for a run than for an ordinary row. The whole point of the
durable run is that a crash leaves the aggregate at the exact point work stopped.
If a resume and a crash-recovery write race, the surviving row must be one of the
two real states, not a blend that never existed — and the losing writer must be
told it lost, so it can reload and retry rather than assume its step settled.

The comparison is deliberate: `SqlAlchemyNoteRepository.save` already expresses
this as a single `UPDATE ... WHERE version = :expected`. These tests pin the same
guarantee for runs, through the write path production uses (`save`, not `add`).
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.use_cases.agent_runs import PlanAgentRun
from notes_service.application.use_cases.agents import (
    GrantDelegation,
    IssueAgentToken,
    RegisterAgent,
    ResolveAgentToken,
)
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.domain.agent.runs import (
    AgentRun,
    AgentRunId,
    PlannedStep,
    RunStatus,
    StepStatus,
)
from notes_service.domain.agent.value_objects import Capability, TokenAudience
from notes_service.domain.errors import VersionConflict
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.security.clock import SystemClock

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)


def _principal() -> Principal:
    return Principal(
        user_id=ALICE,
        email=Email(f"{ALICE}@example.com"),
        display_name="Caller",
        provider="demo",
        subject=str(ALICE),
    )


def _create_step(title: str) -> PlannedStep:
    return PlannedStep(
        tool_name="create_note",
        arguments={"title": title, "content": f"body for {title}"},
    )


def _plan(session_factory: sessionmaker[Session], principal: Principal) -> tuple[AgentRun, str]:
    """Create a real two-step run owned by the fixture user, plus its token."""
    auth = DefaultAuthorizationProvider()
    clock = SystemClock()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    def make(cls, **extra):  # type: ignore[no-untyped-def]
        return cls(uow_factory=uow_factory, authorization=auth, clock=clock, **extra)

    resolve_token = make(ResolveAgentToken)
    agent = make(RegisterAgent).run(
        principal,
        display_name="Racer",
        runtime_identity="svc-racer",
        capabilities=[Capability.CREATE_NOTE.value],
    )
    delegation = make(GrantDelegation).run(
        principal, agent_id=str(agent.id), capabilities=[Capability.CREATE_NOTE.value]
    )
    secret = (
        make(IssueAgentToken)
        .run(
            principal,
            delegation_id=str(delegation.id),
            capabilities=[Capability.CREATE_NOTE.value],
            audience=TokenAudience.TOOL_GATEWAY.value,
            ttl_seconds=300,
        )
        .secret
    )
    run = make(PlanAgentRun, resolve_token=resolve_token).run(
        secret=secret, steps=[_create_step("first"), _create_step("second")]
    )
    return run, secret


def _reload(session_factory: sessionmaker[Session], run_id: AgentRunId) -> AgentRun:
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        loaded = uow.agent_runs.get(run_id)
    assert loaded is not None
    return loaded


def test_two_writers_on_the_same_version_cannot_both_commit(
    session_factory: sessionmaker[Session],
) -> None:
    """A stale write must raise VersionConflict instead of erasing the winner.

    Both writers load the run at the same version — exactly what two requests
    sharing a run id do. The first advances a step; the second, still holding the
    original version, writes a state derived from the stale copy. Before the fix
    both commits succeeded and the first writer's step success vanished.
    """
    principal = _principal()
    run, _secret = _plan(session_factory, principal)

    # Two independent loads, as two concurrent requests would do.
    first = _reload(session_factory, run.id)
    second = _reload(session_factory, run.id)
    assert first.version == second.version

    first.resume()
    first.begin_step(first.ordered_steps[0].id)
    first.complete_step(first.ordered_steps[0].id, outcome="ok")
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.agent_runs.save(first)
        uow.commit()

    # The second writer never saw the first writer's commit.
    second.mark_interrupted()
    with pytest.raises(VersionConflict), SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.agent_runs.save(second)
        uow.commit()

    survivor = _reload(session_factory, run.id)
    assert survivor.ordered_steps[0].status is StepStatus.SUCCEEDED
    assert survivor.version == first.version


def test_a_matching_version_write_still_succeeds(
    session_factory: sessionmaker[Session],
) -> None:
    """The guard must not refuse an honest write from the current version.

    Without this, a compare-and-set that simply always raised would look correct
    in the test above while breaking every real advance.
    """
    principal = _principal()
    run, _secret = _plan(session_factory, principal)

    current = _reload(session_factory, run.id)
    current.resume()
    current.begin_step(current.ordered_steps[0].id)
    current.complete_step(current.ordered_steps[0].id, outcome="ok")
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.agent_runs.save(current)
        uow.commit()

    reloaded = _reload(session_factory, run.id)
    assert reloaded.version == current.version
    assert reloaded.ordered_steps[0].status is StepStatus.SUCCEEDED

    # A sequential second write from the freshly loaded version is honest.
    reloaded.begin_step(reloaded.ordered_steps[1].id)
    reloaded.complete_step(reloaded.ordered_steps[1].id, outcome="ok")
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.agent_runs.save(reloaded)
        uow.commit()

    final = _reload(session_factory, run.id)
    assert final.status is RunStatus.COMPLETED


def test_a_stale_write_cannot_erase_an_interrupted_step(
    session_factory: sessionmaker[Session],
) -> None:
    """The concrete harm: a stale write can hide a step whose effect is unknown.

    A crash marks step 2 ``interrupted``. A writer that loaded the run before the
    crash then commits its stale copy, and the interruption disappears — the row
    claims step 2 is merely ``pending``, so the next resume re-executes an effect
    that may already have landed. That duplicate is exactly what this aggregate
    exists to prevent, so the stale commit must be refused.
    """
    principal = _principal()
    run, _secret = _plan(session_factory, principal)

    # A stale reader grabs the run before any progress is recorded.
    stale = _reload(session_factory, run.id)

    # Step 1 settles, step 2 is entered, then the process dies inside step 2.
    crashed = _reload(session_factory, run.id)
    crashed.resume()
    crashed.begin_step(crashed.ordered_steps[0].id)
    crashed.complete_step(crashed.ordered_steps[0].id, outcome="ok")
    crashed.begin_step(crashed.ordered_steps[1].id)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.agent_runs.save(crashed)
        uow.commit()

    recovery = _reload(session_factory, run.id)
    assert recovery.mark_interrupted() is True
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.agent_runs.save(recovery)
        uow.commit()

    # The stale writer commits its pre-crash copy. It must be refused, or the
    # ambiguity about step 2's effect is silently erased.
    with pytest.raises(VersionConflict), SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.agent_runs.save(stale)
        uow.commit()

    survivor = _reload(session_factory, run.id)
    assert survivor.status is RunStatus.INTERRUPTED
    assert survivor.ordered_steps[1].status is StepStatus.INTERRUPTED
