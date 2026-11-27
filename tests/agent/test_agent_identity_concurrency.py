"""A stale write must not resurrect a suspended or revoked agent (NEX-021).

``AgentIdentity`` carried a ``version`` that every authority-changing transition
already bumped — ``suspend``, ``activate``, ``revoke`` and
``narrow_capabilities`` — but ``SqlAlchemyAgentRepository.save`` ignored it and
rewrote ``status``, ``capabilities``, ``revoked_at`` and the rest from whatever
the caller had last read. The version was decorative.

The harm is the same class NEX-019 fixed for ``agent_runs`` and NEX-020 fixed
for approvals, on the aggregate where revocation *is* the emergency stop. Two
requests can hold the same agent id at once (a human revoking in the control
centre while an unrelated edit finishes). With no compare-and-set the later
commit wins silently:

* a stale copy saved after a revoke writes ``status=active`` and
  ``revoked_at=NULL`` back over the revoked row — the kill switch is undone and
  the agent may issue tokens again;
* a stale copy taken before a narrowing restores capabilities the narrowing
  removed, re-widening a ceiling that may only shrink.

Both are silent re-grants of withdrawn authority, so these tests pin the
guarantee through the production write path (``save``), not ``add``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.use_cases.agents import RegisterAgent
from notes_service.domain.agent.entities import AgentIdentity
from notes_service.domain.agent.value_objects import AgentStatus, Capability
from notes_service.domain.errors import VersionConflict
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)


def _principal(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email(f"{user_id}@example.com"),
        display_name="Caller",
        provider="demo",
        subject=str(user_id),
    )


@pytest.fixture
def uow(session_factory: sessionmaker[Session]) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(session_factory)


@pytest.fixture
def agent(uow: SqlAlchemyUnitOfWork) -> AgentIdentity:
    register = RegisterAgent(
        uow_factory=lambda: uow,
        authorization=None,  # type: ignore[arg-type]
        clock=None,  # type: ignore[arg-type]
    )
    return register.run(
        _principal(ALICE),
        display_name="Worker",
        runtime_identity="svc-worker",
        capabilities=[Capability.READ_NOTE.value, Capability.UPDATE_NOTE.value],
    )


def _reload(uow: SqlAlchemyUnitOfWork, agent: AgentIdentity) -> AgentIdentity:
    with uow:
        loaded = uow.agents.get(agent.id)
    assert loaded is not None
    return loaded


def test_a_stale_write_cannot_resurrect_a_revoked_agent(
    uow: SqlAlchemyUnitOfWork, agent: AgentIdentity
) -> None:
    """The core harm: a stale save restoring a revoked principal.

    A human revokes; a concurrent writer still holds its pre-revocation copy and
    saves it. Before the fix that save wrote ``status=active`` and
    ``revoked_at=NULL`` back, so the emergency stop vanished. It must now raise
    ``VersionConflict`` and the row must stay revoked.
    """
    revoker = _reload(uow, agent)
    stale = _reload(uow, agent)
    assert revoker.version == stale.version

    revoker.revoke()
    with uow:
        uow.agents.save(revoker)
        uow.commit()

    with pytest.raises(VersionConflict), uow:
        uow.agents.save(stale)
        uow.commit()

    survivor = _reload(uow, agent)
    assert survivor.status is AgentStatus.REVOKED
    assert survivor.revoked_at is not None
    assert survivor.version == revoker.version


def test_a_stale_write_cannot_reinstate_suspended_agent(
    uow: SqlAlchemyUnitOfWork, agent: AgentIdentity
) -> None:
    """Suspension is also an authority change, so it gets the same guard.

    A stale ``active`` copy saved over a suspended row re-enables token issuance
    without anyone reactivating the agent.
    """
    suspender = _reload(uow, agent)
    stale = _reload(uow, agent)

    suspender.suspend()
    with uow:
        uow.agents.save(suspender)
        uow.commit()

    with pytest.raises(VersionConflict), uow:
        uow.agents.save(stale)
        uow.commit()

    survivor = _reload(uow, agent)
    assert survivor.status is AgentStatus.SUSPENDED


def test_a_stale_write_cannot_widen_a_narrowed_capability_set(
    uow: SqlAlchemyUnitOfWork, agent: AgentIdentity
) -> None:
    """Narrowing may only shrink the ceiling; a stale save must not undo it.

    The domain refuses to widen in ``narrow_capabilities``, but a stale full-row
    write bypassed the domain entirely and put the removed capability back.
    """
    narrower = _reload(uow, agent)
    stale = _reload(uow, agent)
    assert Capability.UPDATE_NOTE in stale.capabilities

    narrower.narrow_capabilities(capabilities={Capability.READ_NOTE})
    with uow:
        uow.agents.save(narrower)
        uow.commit()

    with pytest.raises(VersionConflict), uow:
        uow.agents.save(stale)
        uow.commit()

    survivor = _reload(uow, agent)
    assert survivor.capabilities == frozenset({Capability.READ_NOTE})


def test_a_matching_version_write_still_succeeds(
    uow: SqlAlchemyUnitOfWork, agent: AgentIdentity
) -> None:
    """The guard must not refuse an honest write from the current version.

    Without this, a compare-and-set that always raised would look correct above
    while breaking every real edit.
    """
    current = _reload(uow, agent)
    current.suspend()
    with uow:
        uow.agents.save(current)
        uow.commit()

    reloaded = _reload(uow, agent)
    assert reloaded.status is AgentStatus.SUSPENDED
    assert reloaded.version == current.version


def test_a_second_save_without_reload_is_refused(
    uow: SqlAlchemyUnitOfWork, agent: AgentIdentity
) -> None:
    """``persisted_version`` advances on write, so a double save cannot pass.

    The aggregate is the truth only for the version it was loaded at. After a
    successful save its ``persisted_version`` moves to the version it left
    behind, so replaying that same object against a row another writer has since
    advanced must conflict rather than silently re-apply the older snapshot.
    """
    first = _reload(uow, agent)
    first.suspend()
    with uow:
        uow.agents.save(first)
        uow.commit()

    # Another writer advances the row.
    other = _reload(uow, agent)
    other.activate()
    with uow:
        uow.agents.save(other)
        uow.commit()

    # ``first`` still believes it is at the version it wrote, which is now stale.
    first.revoke()
    with pytest.raises(VersionConflict), uow:
        uow.agents.save(first)
        uow.commit()

    survivor = _reload(uow, agent)
    assert survivor.status is AgentStatus.ACTIVE
