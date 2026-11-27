"""Revoking authority must also withdraw the approvals granted under it.

An approval is *live authority* from the moment a human grants it until it is
spent or expires. It is keyed by agent and records no delegation of its own, so
before this was enforced nothing tied a grant to the consent that justified it:

* revoking a delegation revoked the delegation and its tokens, but left the
  agent operational. A fresh delegation would then make a previously approved
  action usable again — the agent acted on an authorisation the human granted
  under a consent the human had since withdrawn.
* revoking an agent left its approvals ``approved``. The agent check blocked
  *use* while revoked, so this was latent rather than exploitable — but any
  reactivation path would inherit grants made before the emergency stop.

The fix withdraws granted-but-unspent approvals at both revocation points, so
the human must approve again. These tests pin the behaviour and the two cases
that must *not* change: a spent approval stays spent history, and a pending one
is not silently converted into a revocation.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import AccessDenied
from notes_service.application.use_cases.agents import (
    DecideApprovalRequest,
    GrantDelegation,
    IssueAgentToken,
    OpenApprovalRequest,
    RegisterAgent,
    ResolveAgentToken,
    RevokeAgent,
    RevokeDelegation,
)
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.notes import CreateNote
from notes_service.application.use_cases.tools import InvokeTool
from notes_service.domain.agent.value_objects import ApprovalStatus, Capability, TokenAudience
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.security.clock import SystemClock

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
TOOL_GATEWAY = TokenAudience.TOOL_GATEWAY.value


def principal_for(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email(f"{user_id}@example.com"),
        display_name="Caller",
        provider="demo",
        subject=str(user_id),
    )


@dataclass
class Harness:
    session_factory: sessionmaker[Session]
    principal: Principal
    gateway: InvokeTool
    register: RegisterAgent
    delegate: GrantDelegation
    issue: IssueAgentToken
    open_approval: OpenApprovalRequest
    decide: DecideApprovalRequest
    revoke_delegation: RevokeDelegation
    revoke_agent: RevokeAgent
    create_note: CreateNote

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.session_factory)

    def agent(self, *capabilities: Capability):
        return self.register.run(
            self.principal,
            display_name="Summarizer",
            runtime_identity="svc-summarizer",
            capabilities=[c.value for c in capabilities],
        )

    def delegation(self, agent, *capabilities: Capability):
        return self.delegate.run(
            self.principal,
            agent_id=str(agent.id),
            capabilities=[c.value for c in capabilities],
        )

    def tool_token(self, delegation, *capabilities: Capability) -> str:
        return self.issue.run(
            self.principal,
            delegation_id=str(delegation.id),
            capabilities=[c.value for c in capabilities],
            audience=TOOL_GATEWAY,
            ttl_seconds=300,
        ).secret

    def approved(self, agent, capability: Capability, resource_ref: str):
        """A request opened for ``agent`` and approved by its owner."""
        request = self.open_approval.run(
            self.principal,
            agent_id=str(agent.id),
            capability=capability.value,
            resource_ref=resource_ref,
        )
        return self.decide.run(self.principal, approval_id=str(request.id), approve=True)


@pytest.fixture
def h(session_factory: sessionmaker[Session]) -> Harness:
    auth = DefaultAuthorizationProvider()
    clock = SystemClock()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    def make(cls, **extra):  # type: ignore[no-untyped-def]
        return cls(uow_factory=uow_factory, authorization=auth, clock=clock, **extra)

    resolve = make(ResolveAgentToken)
    return Harness(
        session_factory=session_factory,
        principal=principal_for(ALICE),
        gateway=make(InvokeTool, resolve_token=resolve),
        register=make(RegisterAgent),
        delegate=make(GrantDelegation),
        issue=make(IssueAgentToken),
        open_approval=make(OpenApprovalRequest),
        decide=make(DecideApprovalRequest),
        revoke_delegation=make(RevokeDelegation),
        revoke_agent=make(RevokeAgent),
        create_note=make(CreateNote),
    )


class TestRevokingDelegationWithdrawsApprovals:
    def test_granted_approval_is_revoked_with_its_delegation(self, h: Harness) -> None:
        agent = h.agent(Capability.DELETE_NOTE)
        delegation = h.delegation(agent, Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="Doomed", content="body")
        granted = h.approved(agent, Capability.DELETE_NOTE, f"note:{note.id}")
        assert granted.status is ApprovalStatus.APPROVED

        h.revoke_delegation.run(h.principal, delegation_id=str(delegation.id))

        with h.uow() as uow:
            stored = uow.approvals.get(granted.id)
        assert stored is not None
        assert stored.status is ApprovalStatus.REVOKED
        assert stored.revoked_at is not None
        assert stored.revoke_reason == f"delegation {delegation.id} revoked"
        # No human withdrew this, so no user id is invented for the stamp.
        assert stored.revoked_by_user_id is None

    def test_a_fresh_delegation_cannot_revive_the_old_approval(self, h: Harness) -> None:
        """The regression this slice exists to prevent.

        Revocation left the agent operational, so re-delegating made the
        previously approved action usable again without the human being asked a
        second time. The approval must stay dead across a new delegation.
        """
        agent = h.agent(Capability.DELETE_NOTE)
        first = h.delegation(agent, Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="Doomed", content="body")
        h.approved(agent, Capability.DELETE_NOTE, f"note:{note.id}")

        h.revoke_delegation.run(h.principal, delegation_id=str(first.id))

        second = h.delegation(agent, Capability.DELETE_NOTE)
        secret = h.tool_token(second, Capability.DELETE_NOTE)

        with pytest.raises(AccessDenied, match="requires human approval"):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(note.id)},
                idempotency_key="k-after-revoke",
            )

    def test_a_spent_approval_is_left_as_history(self, h: Harness) -> None:
        """Revocation must not rewrite an approval whose action already ran."""
        agent = h.agent(Capability.DELETE_NOTE)
        delegation = h.delegation(agent, Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="Doomed", content="body")
        granted = h.approved(agent, Capability.DELETE_NOTE, f"note:{note.id}")
        secret = h.tool_token(delegation, Capability.DELETE_NOTE)
        h.gateway.run(
            secret=secret,
            tool_name="delete_note",
            arguments={"note_id": str(note.id)},
            idempotency_key="k-spend",
        )

        h.revoke_delegation.run(h.principal, delegation_id=str(delegation.id))

        with h.uow() as uow:
            stored = uow.approvals.get(granted.id)
        assert stored is not None
        assert stored.status is ApprovalStatus.APPROVED
        assert stored.consumed_at is not None
        assert stored.revoked_at is None

    def test_a_pending_request_is_not_marked_revoked(self, h: Harness) -> None:
        """A pending request was never a grant, so it is not a withdrawn grant.

        Collapsing the two would erase the difference between "asked and never
        decided" and "granted then taken back" from the audit record.
        """
        agent = h.agent(Capability.DELETE_NOTE)
        delegation = h.delegation(agent, Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="Doomed", content="body")
        pending = h.open_approval.run(
            h.principal,
            agent_id=str(agent.id),
            capability=Capability.DELETE_NOTE.value,
            resource_ref=f"note:{note.id}",
        )

        h.revoke_delegation.run(h.principal, delegation_id=str(delegation.id))

        with h.uow() as uow:
            stored = uow.approvals.get(pending.id)
        assert stored is not None
        assert stored.status is ApprovalStatus.PENDING
        assert stored.revoked_at is None


class TestRevokingAgentWithdrawsApprovals:
    def test_granted_approval_is_revoked_with_its_agent(self, h: Harness) -> None:
        agent = h.agent(Capability.DELETE_NOTE)
        h.delegation(agent, Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="Doomed", content="body")
        granted = h.approved(agent, Capability.DELETE_NOTE, f"note:{note.id}")

        h.revoke_agent.run(h.principal, agent_id=str(agent.id))

        with h.uow() as uow:
            stored = uow.approvals.get(granted.id)
        assert stored is not None
        assert stored.status is ApprovalStatus.REVOKED
        assert stored.revoke_reason == "agent revoked"

    def test_a_spent_approval_survives_the_agent_revocation(self, h: Harness) -> None:
        agent = h.agent(Capability.DELETE_NOTE)
        delegation = h.delegation(agent, Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="Doomed", content="body")
        granted = h.approved(agent, Capability.DELETE_NOTE, f"note:{note.id}")
        secret = h.tool_token(delegation, Capability.DELETE_NOTE)
        h.gateway.run(
            secret=secret,
            tool_name="delete_note",
            arguments={"note_id": str(note.id)},
            idempotency_key="k-spend",
        )

        h.revoke_agent.run(h.principal, agent_id=str(agent.id))

        with h.uow() as uow:
            stored = uow.approvals.get(granted.id)
        assert stored is not None
        assert stored.status is ApprovalStatus.APPROVED
        assert stored.consumed_at is not None
