"""A stale decision must not re-grant authority a human withdrew (NEX-020).

``ApprovalRequest`` is not an ordinary row: it *is* live authority. Approve,
deny, revoke and consume each change whether the row authorises an action, and
until this change the row carried no version at all —
``SqlAlchemyApprovalRequestRepository.save`` rewrote ``status``, ``consumed_at``,
``revoked_at`` and the rest from whatever the caller had loaded.

Two requests can hold the same request id at the same time (a human revoking in
one tab while an agent's decide path finishes in another). With no
compare-and-set the later commit wins silently, and the loser's intent is
inverted:

* a stale ``approved`` copy saved after a revoke writes ``status=approved`` back
  over a ``revoked`` row — the withdrawal disappears and the agent may act on
  authority a human explicitly took away;
* a stale copy taken before ``consume`` clears ``consumed_at``, so a single-use
  approval becomes spendable a second time.

Both are silent re-grants of withdrawn authority, so they are security defects,
not lost-update cosmetics. NEX-019 fixed the same class for ``agent_runs``; these
tests pin the identical guarantee for approvals through the production write path
(``save``), not ``add``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.use_cases.agents import RegisterAgent
from notes_service.domain.agent.entities import ApprovalRequest
from notes_service.domain.agent.value_objects import ApprovalStatus, Capability
from notes_service.domain.errors import VersionConflict
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
BOB = UserId(DEMO_USERS[1].id)


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
def agent(uow: SqlAlchemyUnitOfWork):
    register = RegisterAgent(
        uow_factory=lambda: uow,
        authorization=None,  # type: ignore[arg-type]
        clock=None,  # type: ignore[arg-type]
    )
    return register.run(
        _principal(ALICE),
        display_name="Approver",
        runtime_identity="svc-approver",
        capabilities=[Capability.DELETE_NOTE.value],
    )


def _open_approved(uow: SqlAlchemyUnitOfWork, agent, *, approver: UserId = BOB) -> ApprovalRequest:
    """Persist an approved request the way production does: open, decide, save."""
    request = ApprovalRequest.open(
        agent=agent,
        capability=Capability.DELETE_NOTE,
        resource_ref="note:abc",
    )
    with uow:
        uow.approvals.add(request)
        uow.commit()

    request.approve(approver_user_id=str(approver), reason="ok")
    with uow:
        uow.approvals.save(request)
        uow.commit()
    return request


def _reload(uow: SqlAlchemyUnitOfWork, request: ApprovalRequest) -> ApprovalRequest:
    with uow:
        loaded = uow.approvals.get(request.id)
    assert loaded is not None
    return loaded


def test_a_stale_approve_cannot_resurrect_a_revoked_approval(
    uow: SqlAlchemyUnitOfWork, agent
) -> None:
    """The core harm: a stale writer restoring withdrawn authority.

    A human revokes; a concurrent writer still holds its pre-revocation copy and
    saves it. Before the fix that save flipped the row back to ``approved`` and
    the revocation was gone. It must now raise ``VersionConflict`` and the row
    must stay revoked.
    """
    request = _open_approved(uow, agent)

    # Two independent loads, as two concurrent requests would do.
    revoker = _reload(uow, request)
    stale = _reload(uow, request)
    assert revoker.version == stale.version

    revoker.revoke(revoked_by_user_id=str(ALICE), reason="wrong resource")
    with uow:
        uow.approvals.save(revoker)
        uow.commit()

    # The stale writer never saw the revocation.
    with pytest.raises(VersionConflict), uow:
        uow.approvals.save(stale)
        uow.commit()

    survivor = _reload(uow, request)
    assert survivor.status is ApprovalStatus.REVOKED
    assert survivor.revoked_at is not None
    assert survivor.revoke_reason == "wrong resource"
    assert survivor.version == revoker.version


def test_a_stale_write_cannot_clear_a_spent_approval(uow: SqlAlchemyUnitOfWork, agent) -> None:
    """The double-spend harm: a stale save erasing the single-use stamp.

    ``consume`` is already atomic, but it did not bump the version, so a stale
    ``save`` could still match and write ``consumed_at = NULL`` back — making an
    already-spent single-use approval spendable again. ``consume`` now advances
    the version, so the stale save is refused.
    """
    request = _open_approved(uow, agent)
    stale = _reload(uow, request)

    with uow:
        assert uow.approvals.consume(request.id, now=datetime.now(UTC)) is True
        uow.commit()

    spent = _reload(uow, request)
    assert spent.consumed_at is not None
    assert spent.version > stale.version

    with pytest.raises(VersionConflict), uow:
        uow.approvals.save(stale)
        uow.commit()

    survivor = _reload(uow, request)
    assert survivor.consumed_at is not None, "a spent approval must stay spent"


def test_a_matching_version_write_still_succeeds(uow: SqlAlchemyUnitOfWork, agent) -> None:
    """The guard must not refuse an honest write from the current version.

    Without this, a compare-and-set that always raised would look correct above
    while breaking every real decision.
    """
    request = _open_approved(uow, agent)

    current = _reload(uow, request)
    current.revoke(revoked_by_user_id=str(ALICE), reason="wrong resource")
    with uow:
        uow.approvals.save(current)
        uow.commit()

    reloaded = _reload(uow, request)
    assert reloaded.status is ApprovalStatus.REVOKED
    assert reloaded.version == current.version


def test_version_advances_on_every_decision(agent) -> None:
    """Every transition that changes authority must bump the version.

    If any of these forgot to bump, the compare-and-set above would still let a
    stale copy of that transition overwrite it — the exact hole being closed.
    Revoke and consume are mutually exclusive end states, so each gets its own
    request rather than one chain.
    """
    approved = ApprovalRequest.open(
        agent=agent,
        capability=Capability.DELETE_NOTE,
        resource_ref="note:abc",
    )
    opened = approved.version
    approved.approve(approver_user_id=str(BOB), reason="ok")
    assert approved.version > opened

    revoked = ApprovalRequest.open(
        agent=agent,
        capability=Capability.DELETE_NOTE,
        resource_ref="note:abc",
    )
    revoked.approve(approver_user_id=str(BOB), reason="ok")
    before_revoke = revoked.version
    revoked.revoke(revoked_by_user_id=str(ALICE), reason="late")
    assert revoked.version > before_revoke

    spent = ApprovalRequest.open(
        agent=agent,
        capability=Capability.DELETE_NOTE,
        resource_ref="note:abc",
    )
    spent.approve(approver_user_id=str(BOB), reason="ok")
    before_consume = spent.version
    spent.consume(now=datetime.now(UTC))
    assert spent.version > before_consume


def test_a_stale_consume_cannot_double_spend_through_save(uow: SqlAlchemyUnitOfWork, agent) -> None:
    """End to end: two spends of one single-use approval, one must lose.

    The atomic ``consume`` protects the direct path. This pins the *save* path:
    a writer holding a pre-spend copy must not be able to write the un-spent row
    back and re-open the window.
    """
    request = _open_approved(uow, agent)
    stale = _reload(uow, request)

    with uow:
        first = uow.approvals.consume(request.id, now=datetime.now(UTC))
        uow.commit()
    assert first is True

    with uow:
        second = uow.approvals.consume(request.id, now=datetime.now(UTC))
        uow.commit()
    assert second is False, "the atomic predicate already blocks the second spend"

    # The stale save must also be refused, so it cannot undo the first spend.
    with pytest.raises(VersionConflict), uow:
        uow.approvals.save(stale)
        uow.commit()

    assert _reload(uow, request).consumed_at is not None
