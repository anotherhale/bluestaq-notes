"""An approval must be judged against the clock that stamped it.

``ApprovalRequest.open`` derives ``expires_at`` from the timestamp it is given,
and every use case in production passes its own ``Clock`` port. The decision
path used to ask ``is_expired``, which reads the *wall* clock. Two clocks, one
comparison: whenever they disagree — an injected test clock, a frozen clock in a
worker, or plain skew between nodes — the answer is wrong in both directions.

* A request opened on a clock behind the wall clock is judged already lapsed, so
  a human cannot decide a request that is in fact still valid.
* A request opened on a clock ahead of the wall clock is judged still valid after
  its bound has passed, so authority outlives the consent that granted it.

These tests pin the single-clock invariant at the aggregate and at the gateway.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.use_cases.agents import RegisterAgent
from notes_service.domain.agent.entities import DEFAULT_APPROVAL_TTL, ApprovalRequest
from notes_service.domain.agent.value_objects import Capability
from notes_service.domain.errors import InvariantViolation
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)

# Far enough from the wall clock that a wall-clock comparison cannot coincide.
LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)
FAR_AHEAD = datetime(2099, 1, 1, tzinfo=UTC)


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
def agent(uow):
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


def _open(agent, *, now: datetime) -> ApprovalRequest:
    return ApprovalRequest.open(
        agent=agent,
        capability=Capability.DELETE_NOTE,
        resource_ref="note:abc",
        now=now,
    )


class TestADecisionUsesTheOpeningClock:
    def test_a_request_opened_in_the_past_is_still_decidable(self, agent) -> None:
        """A clock behind the wall clock must not make a live request undecidable.

        Before the fix this raised: the bound was stamped from ``LONG_AGO`` but
        ``is_expired`` compared it against ``datetime.now()``, which is always
        past it.
        """
        request = _open(agent, now=LONG_AGO)
        assert request.expired_at(now=LONG_AGO) is False

        request.approve(approver_user_id=ALICE, now=LONG_AGO + timedelta(seconds=1))
        assert request.is_usable(now=LONG_AGO + timedelta(seconds=1)) is True

    def test_a_lapsed_request_is_refused_even_when_the_wall_clock_disagrees(self, agent) -> None:
        """A clock ahead of the wall clock must not keep authority alive.

        Before the fix this *succeeded*: the bound was stamped from ``FAR_AHEAD``
        so the wall clock had not reached it, and a request the caller's own
        clock says has lapsed was approved anyway.
        """
        request = _open(agent, now=FAR_AHEAD)
        lapsed = FAR_AHEAD + DEFAULT_APPROVAL_TTL + timedelta(seconds=1)
        assert request.expired_at(now=lapsed) is True

        with pytest.raises(InvariantViolation, match="expired"):
            request.approve(approver_user_id=ALICE, now=lapsed)

    def test_deny_also_uses_the_caller_clock(self, agent) -> None:
        request = _open(agent, now=FAR_AHEAD)
        lapsed = FAR_AHEAD + DEFAULT_APPROVAL_TTL + timedelta(seconds=1)
        with pytest.raises(InvariantViolation, match="expired"):
            request.deny(approver_user_id=ALICE, now=lapsed)

    def test_the_decision_timestamp_is_the_one_supplied(self, agent) -> None:
        """``decided_at`` must be the caller's instant, not a second reading."""
        decided = LONG_AGO + timedelta(minutes=1)
        request = _open(agent, now=LONG_AGO)
        request.approve(approver_user_id=ALICE, now=decided)
        assert request.decided_at == decided


class TestTheWallClockPropertyStillFailsClosed:
    def test_is_expired_reads_the_wall_clock(self, agent) -> None:
        """The property is kept for callers with no clock; it must stay strict."""
        fresh = _open(agent, now=datetime.now(UTC))
        assert fresh.is_expired is False

        stale = _open(agent, now=LONG_AGO)
        assert stale.is_expired is True

    def test_a_missing_bound_is_expired_at_any_instant(self, agent) -> None:
        request = _open(agent, now=LONG_AGO)
        request.expires_at = None
        assert request.expired_at(now=LONG_AGO) is True
        assert request.expired_at(now=FAR_AHEAD) is True
