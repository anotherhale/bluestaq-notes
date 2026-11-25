"""Does an approval's time bound survive the round trip through the database?

NEX-013 bounded an approval in time: ``ApprovalRequest.open`` stamps
``expires_at`` so a human decision lapses after ``DEFAULT_APPROVAL_TTL``.
That bound is only real if the adapter persists it. The gateway writes the
request through ``ApprovalRequestRepository.add`` and reads it back through
``list_for_agent``, so those two methods are the only production path.

The expiry test in ``test_tool_gateway.py`` cannot see a defect here: it
approves the request and then calls ``save()``, which is a *different* method
that does persist ``expires_at``. A repository that dropped the column on
``add`` would leave that test green while every approval opened by the gateway
was unbounded.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.use_cases.agents import RegisterAgent
from notes_service.domain.agent.entities import DEFAULT_APPROVAL_TTL, ApprovalRequest
from notes_service.domain.agent.value_objects import ApprovalStatus, Capability
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
def agent(uow: SqlAlchemyUnitOfWork):
    register = RegisterAgent(
        uow_factory=lambda: uow,
        authorization=None,  # type: ignore[arg-type]
        clock=None,  # type: ignore[arg-type]
    )
    return register.run(
        _principal(ALICE),
        display_name="Deleter",
        runtime_identity="svc-deleter",
        capabilities=[Capability.DELETE_NOTE.value],
    )


class TestApprovalTimeBoundSurvivesPersistence:
    def test_open_then_read_back_keeps_expires_at(self, uow, agent) -> None:
        """The TTL stamped at open time must be readable after a round trip."""
        request = ApprovalRequest.open(
            agent=agent,
            capability=Capability.DELETE_NOTE,
            resource_ref="note:abc",
        )
        assert request.expires_at is not None, "open() must stamp a bound"

        with uow:
            uow.approvals.add(request)
            uow.commit()

        with uow:
            stored = uow.approvals.get(request.id)

        assert stored is not None
        assert stored.expires_at is not None, (
            "expires_at was dropped by the repository; the approval is unbounded"
        )
        assert stored.expires_at == request.expires_at
        # The bound is exactly the configured TTL, not merely non-null.
        assert stored.expires_at - stored.requested_at == DEFAULT_APPROVAL_TTL

    def test_a_reloaded_approval_still_expires(self, uow, agent) -> None:
        """``is_usable`` must return False once the stored bound has lapsed."""
        request = ApprovalRequest.open(
            agent=agent,
            capability=Capability.DELETE_NOTE,
            resource_ref="note:abc",
        )
        with uow:
            uow.approvals.add(request)
            uow.commit()

        with uow:
            stored = uow.approvals.get(request.id)
            assert stored is not None
            stored.approve(approver_user_id=ALICE, now=stored.requested_at)
            uow.approvals.save(stored)
            uow.commit()

        with uow:
            reloaded = uow.approvals.get(request.id)
            assert reloaded is not None
            past = reloaded.requested_at + DEFAULT_APPROVAL_TTL + timedelta(seconds=1)
            assert reloaded.is_usable(now=past) is False, (
                "an approval past its bound must not be usable"
            )


class TestAMissingBoundFailsClosed:
    """A NULL ``expires_at`` must deny, not grant indefinite authority.

    The column is nullable for rows written before it existed. If a missing
    bound were read as "never expires", any path that failed to persist the
    column would turn a bounded approval into a permanent one with no error.
    """

    def test_a_missing_bound_is_not_usable(self, agent) -> None:
        request = ApprovalRequest.open(
            agent=agent,
            capability=Capability.DELETE_NOTE,
            resource_ref="note:abc",
        )
        request.approve(approver_user_id=ALICE, now=request.requested_at)
        request.expires_at = None
        assert request.is_usable() is False

    def test_a_missing_bound_counts_as_expired(self, agent) -> None:
        request = ApprovalRequest.open(
            agent=agent,
            capability=Capability.DELETE_NOTE,
            resource_ref="note:abc",
        )
        request.expires_at = None
        assert request.is_expired is True

    def test_the_repository_refuses_to_spend_an_unbounded_row(self, uow, agent) -> None:
        """The conditional UPDATE must not match a row with no recorded bound."""
        request = ApprovalRequest.open(
            agent=agent,
            capability=Capability.DELETE_NOTE,
            resource_ref="note:abc",
        )
        with uow:
            uow.approvals.add(request)
            uow.commit()

        with uow:
            stored = uow.approvals.get(request.id)
            assert stored is not None
            stored.approve(approver_user_id=ALICE, now=stored.requested_at)
            # Simulate a legacy row written before the column was persisted.
            stored.expires_at = None
            uow.approvals.save(stored)
            uow.commit()

        with uow:
            assert uow.approvals.consume(request.id, now=stored.requested_at) is False


class TestRevocationSurvivesPersistence:
    """The withdrawal must be readable back, with both audit reasons intact.

    ``revoke()`` fixes a defect where the revocation overwrote the approver's
    justification. That fix lives in the entity, but a repository that failed to
    persist ``revoke_reason`` would make it invisible after a reload — and the
    whole point of the field is the audit trail, which is only ever read back.
    """

    def _approved(self, uow, agent) -> ApprovalRequest:
        request = ApprovalRequest.open(
            agent=agent,
            capability=Capability.DELETE_NOTE,
            resource_ref="note:abc",
        )
        with uow:
            uow.approvals.add(request)
            uow.commit()

        with uow:
            stored = uow.approvals.get(request.id)
            assert stored is not None
            stored.approve(
                approver_user_id=ALICE,
                now=stored.requested_at,
                reason="customer signed off",
            )
            uow.approvals.save(stored)
            uow.commit()
        return stored

    def test_a_reloaded_row_reports_revoked(self, uow, agent) -> None:
        approved = self._approved(uow, agent)

        with uow:
            stored = uow.approvals.get(approved.id)
            assert stored is not None
            stored.revoke(
                revoked_by_user_id=ALICE,
                now=stored.requested_at,
                reason="customer withdrew consent",
            )
            uow.approvals.save(stored)
            uow.commit()

        with uow:
            reloaded = uow.approvals.get(approved.id)

        assert reloaded is not None
        assert reloaded.status is ApprovalStatus.REVOKED
        assert reloaded.revoked_at is not None
        assert reloaded.revoked_by_user_id == ALICE
        assert reloaded.is_usable() is False

    def test_a_reloaded_row_keeps_both_reasons(self, uow, agent) -> None:
        """Grant justification and withdrawal reason are separate facts."""
        approved = self._approved(uow, agent)

        with uow:
            stored = uow.approvals.get(approved.id)
            assert stored is not None
            stored.revoke(
                revoked_by_user_id=ALICE,
                now=stored.requested_at,
                reason="customer withdrew consent",
            )
            uow.approvals.save(stored)
            uow.commit()

        with uow:
            reloaded = uow.approvals.get(approved.id)

        assert reloaded is not None
        assert reloaded.reason == "customer signed off"
        assert reloaded.revoke_reason == "customer withdrew consent"
        assert reloaded.decided_by_user_id == ALICE
