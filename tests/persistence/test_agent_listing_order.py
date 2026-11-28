"""Paged agent listings must order deterministically under timestamp ties (NEX-093).

Two listings page with an ``OFFSET`` window over a non-unique timestamp: approval
requests on ``requested_at`` and tool invocations on ``invoked_at``. NEX-090 gave
the agent-run listing ``id`` as its tiebreaker; these two still ordered on the
timestamp alone, so two rows written in the same instant had no defined order
between them, and an ``OFFSET`` window over an undefined order can show one row
twice while skipping another as the windows shift.

These tests pin the tiebreakers by *reversing* insertion order against the order
the query must produce. A database that returned rows in insertion order would
fail the assertion, which is the only way to tell a real tiebreaker from a query
that merely looks ordered when rows happen to be inserted in the expected
sequence. The paging tests walk every page, because a repeated-or-skipped row is
the actual defect and a single-page assertion cannot see it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.use_cases.agents import RegisterAgent
from notes_service.domain.agent.entities import ApprovalRequest
from notes_service.domain.agent.tools import (
    Parameter,
    ParameterType,
    ToolDefinition,
    ToolInvocation,
    ToolInvocationId,
)
from notes_service.domain.agent.value_objects import ApprovalStatus, Capability
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
BOB = UserId(DEMO_USERS[1].id)

# One instant for every row: the tie is the whole point.
TIED_AT = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)


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
        display_name="Auditor",
        runtime_identity="svc-auditor",
        capabilities=[Capability.DELETE_NOTE.value],
    )


def _delete_tool() -> ToolDefinition:
    return ToolDefinition(
        name="delete_note",
        capability=Capability.DELETE_NOTE,
        description="Delete one note.",
        parameters=(Parameter(name="note_id", type=ParameterType.STRING, required=True),),
    )


def _invocation(
    agent_id, sequence: int, invocation_id: ToolInvocationId, invoked_at: datetime
) -> ToolInvocation:
    """One invocation whose audit timestamp is pinned, so ties are guaranteed.

    ``begin`` reads the wall clock, so two calls a millisecond apart would not tie
    and the test would pass with or without a tiebreaker. ``invoked_at`` is sealed
    into ``intent_hash``, so it is set before construction rather than patched on
    afterwards.
    """
    begun = ToolInvocation.begin(
        agent_id=agent_id,
        token_id=None,
        tool=_delete_tool(),
        idempotency_key=f"key-{sequence}",
        arguments={"note_id": f"note:{sequence}"},
        resource_ref=f"note:{sequence}",
        invocation_id=invocation_id,
        chain_sequence=sequence,
    )
    return replace(begun, invoked_at=invoked_at)


def _scrambled(values: list) -> list:
    """Insert in an order that is neither the expected one nor its reverse.

    Reversing alone is not enough to prove a tiebreaker exists: SQLite breaks an
    unordered tie by scanning the index backwards, which reproduces reversed
    insertion and would make a query with no tiebreaker look correct. This fixed
    permutation is non-monotone in both directions, so no incidental scan order
    satisfies the expected result.
    """
    order = [1, 3, 5, 0, 2, 4]
    return [values[index] for index in order if index < len(values)]


class TestApprovalListingBreaksTimestampTies:
    """``requested_at`` alone does not order two approvals opened together."""

    def test_paging_visits_every_approval_once(self, uow, agent) -> None:
        """Walking the pages must return each approval exactly once, in id order.

        With an undefined order between tied rows, ``OFFSET`` windows can overlap
        or leave a gap, so a report built from the pages silently loses or
        duplicates an approval.
        """
        ids = sorted(ToolInvocationId.new() for _ in range(6))

        with uow:
            for approval_id in _scrambled(ids):
                uow.approvals.add(
                    ApprovalRequest.open(
                        agent=agent,
                        capability=Capability.DELETE_NOTE,
                        resource_ref=f"note:{approval_id}",
                        approval_id=approval_id,
                        now=TIED_AT,
                    )
                )
            uow.commit()

        seen: list[str] = []
        for offset in range(0, len(ids), 2):
            with uow:
                page = uow.approvals.list_for_agent(agent.id, limit=2, offset=offset)
            seen.extend(str(request.id) for request in page)

        assert seen == [str(value) for value in ids], (
            "paging over tied requested_at values returned a row twice, skipped one, "
            "or ordered by insertion instead of id"
        )

    def test_ties_are_ordered_by_id_not_insertion(self, uow, agent) -> None:
        """The tiebreaker must be ``id``, so insertion order cannot decide the page."""
        ids = sorted(ToolInvocationId.new() for _ in range(4))

        with uow:
            for approval_id in _scrambled(ids):
                uow.approvals.add(
                    ApprovalRequest.open(
                        agent=agent,
                        capability=Capability.DELETE_NOTE,
                        resource_ref=f"note:{approval_id}",
                        approval_id=approval_id,
                        now=TIED_AT,
                    )
                )
            uow.commit()

        with uow:
            listed = uow.approvals.list_for_agent(agent.id)

        assert [str(request.id) for request in listed] == [str(value) for value in ids], (
            "tied approvals must come back ordered by id; insertion order decided instead"
        )

    def test_a_status_page_holds_only_that_status(self, uow, agent) -> None:
        """A page of one status must be a page of that status, not of the history."""
        with uow:
            pending = ApprovalRequest.open(
                agent=agent,
                capability=Capability.DELETE_NOTE,
                resource_ref="note:pending",
                now=TIED_AT,
            )
            approved = ApprovalRequest.open(
                agent=agent,
                capability=Capability.DELETE_NOTE,
                resource_ref="note:approved",
                now=TIED_AT,
            )
            approved.approve(approver_user_id=ALICE, now=TIED_AT + timedelta(seconds=1))
            uow.approvals.add(pending)
            uow.approvals.add(approved)
            uow.commit()

        with uow:
            page = uow.approvals.list_for_agent(agent.id, status=ApprovalStatus.PENDING, limit=10)

        assert [request.resource_ref for request in page] == ["note:pending"]


class TestToolInvocationListingBreaksTimestampTies:
    """The audit listing pages on ``invoked_at``; the chain sequence breaks ties."""

    def test_paging_visits_every_invocation_once(self, uow, agent) -> None:
        """Newest first must not repeat or drop an invocation across pages."""
        now = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)
        invocations = [
            _invocation(agent.id, sequence, ToolInvocationId.new(), now) for sequence in range(1, 7)
        ]
        assert len(invocations) == 6

        with uow:
            for invocation in _scrambled(invocations):
                uow.tool_invocations.add(invocation)
            uow.commit()

        seen: list[int] = []
        for offset in range(0, len(invocations), 2):
            with uow:
                page = uow.tool_invocations.list_for_agent(agent.id, limit=2, offset=offset)
            seen.extend(invocation.chain_sequence for invocation in page)

        assert seen == [6, 5, 4, 3, 2, 1], (
            "paging over tied invoked_at values returned an invocation twice, "
            "skipped one, or ordered by insertion instead of the chain"
        )

    def test_ties_are_ordered_by_chain_sequence_descending(self, uow, agent) -> None:
        """Newest first, and within one instant the higher chain position first.

        The invocations are inserted in a scrambled order, so the expected output
        can only come from the query's tiebreaker. ``id`` would also be a legal
        tiebreaker for paging, which is why this asserts the chain order: the
        audit listing should line up with the chain a verifier walks.
        """
        now = datetime(2026, 11, 28, 10, 30, tzinfo=UTC)
        ordered = [
            _invocation(agent.id, sequence, ToolInvocationId.new(), now) for sequence in range(1, 5)
        ]

        with uow:
            for invocation in _scrambled(ordered):
                uow.tool_invocations.add(invocation)
            uow.commit()

        with uow:
            listed = uow.tool_invocations.list_for_agent(agent.id)

        assert [invocation.chain_sequence for invocation in listed] == [4, 3, 2, 1], (
            "tied invocations must come back in descending chain order, not insertion order"
        )


def test_pending_listing_also_breaks_timestamp_ties(uow, agent) -> None:
    """``list_pending_for_agent`` feeds callers that index ``[0]``.

    Those callers take "the first pending request" as fact, so an undefined order
    there does not merely render differently run to run: it decides which
    approval a caller acts on.
    """
    ids = sorted(ToolInvocationId.new() for _ in range(4))

    with uow:
        for approval_id in _scrambled(ids):
            uow.approvals.add(
                ApprovalRequest.open(
                    agent=agent,
                    capability=Capability.DELETE_NOTE,
                    resource_ref=f"note:{approval_id}",
                    approval_id=approval_id,
                    now=TIED_AT,
                )
            )
        uow.commit()

    with uow:
        pending = uow.approvals.list_pending_for_agent(agent.id)

    assert [str(request.id) for request in pending] == [str(value) for value in ids], (
        "pending approvals must come back in a stable order; [0] was decided by insertion"
    )


def test_another_agent_sees_nothing_from_either_listing(uow, agent) -> None:
    """A tiebreaker must not have widened either listing's agent scope."""
    with uow:
        uow.approvals.add(
            ApprovalRequest.open(
                agent=agent,
                capability=Capability.DELETE_NOTE,
                resource_ref="note:alice",
                now=TIED_AT,
            )
        )
        uow.commit()

    register = RegisterAgent(
        uow_factory=lambda: uow,
        authorization=None,  # type: ignore[arg-type]
        clock=None,  # type: ignore[arg-type]
    )
    bobs_agent = register.run(
        _principal(BOB),
        display_name="Other",
        runtime_identity="svc-other",
        capabilities=[Capability.DELETE_NOTE.value],
    )

    with uow:
        assert uow.approvals.list_for_agent(bobs_agent.id) == []
        assert uow.tool_invocations.list_for_agent(bobs_agent.id) == []
