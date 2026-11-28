"""A two-person approval must be completable, or it is not a control (NEX-071).

The approval aggregate enforces separation of duties correctly: the second
signature must come from a different human than the first. The *authority* to
give that second signature, however, did not exist. ``DecideApprovalRequest``
admitted only the agent's owner, and the owner cannot sign twice, so every
request opened with ``required_approvals == 2`` — every ``second-approver``
policy (NEX-059) and every ``HIGH``-risk capability (NEX-063) — was permanently
stuck. The obligation was recorded, reported as enforced, and unsatisfiable
through the API. A control nobody can complete is worse than an absent one: it
fails closed on the business process while the audit trail says the control ran.

The fix admits a *tenant approver*: a human holding ``APPROVE_AGENT_ACTION`` in
an organization the owner also belongs to. These tests pin both halves — that
the control can now complete, and that the new authority is bounded:

* it reaches only a half-approved request (never the first signature);
* it is bounded by a shared, operational tenant;
* it excludes read-only roles (security admin, auditor, member);
* it never lets the same human sign twice;
* it is still 404, not 403, for a caller with no authority at all.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import ResourceNotFound
from notes_service.application.use_cases.agents import (
    DecideApprovalRequest,
    OpenApprovalRequest,
    RegisterAgent,
)
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.domain.agent.entities import ApprovalRequest
from notes_service.domain.agent.value_objects import (
    ApprovalStatus,
    Capability,
)
from notes_service.domain.errors import InvariantViolation
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.domain.organization.entities import Organization, OrganizationMembership
from notes_service.domain.organization.value_objects import (
    OrganizationRole,
    OrganizationStatus,
)
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.security.clock import SystemClock

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
BOB = UserId(DEMO_USERS[1].id)

# A sensitive capability whose policy floor is two signatures.
SENSITIVE = Capability.DELETE_NOTE


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
    register: RegisterAgent
    open_approval: OpenApprovalRequest
    decide: DecideApprovalRequest

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.session_factory)

    def agent(self, owner: Principal, *capabilities: Capability):
        return self.register.run(
            owner,
            display_name="Summarizer",
            runtime_identity="svc-summarizer",
            capabilities=[c.value for c in capabilities],
        )

    def two_person_request(self, owner: Principal, agent):
        """An approval that needs two distinct humans, signed once by the owner."""
        request = self.open_approval.run(
            owner,
            agent_id=str(agent.id),
            capability=SENSITIVE.value,
            resource_ref="note:all",
        )
        with self.uow() as uow:
            stored = uow.approvals.get(request.id)
            assert stored is not None
            stored.required_approvals = 2
            uow.approvals.save(stored)
            uow.commit()
        return self.decide.run(owner, approval_id=str(request.id), approve=True)

    def tenant(self, *members: tuple[UserId, OrganizationRole]) -> None:
        """One organization with the given (user, role) memberships."""
        with self.uow() as uow:
            organization = Organization.create(
                slug=f"org-{len(members)}-{members[0][0]}", name="Acme"
            )
            uow.organizations.add(organization)
            for user_id, role in members:
                uow.organization_memberships.add(
                    OrganizationMembership.grant(
                        organization_id=organization.id, user_id=str(user_id), role=role
                    )
                )
            uow.commit()


@pytest.fixture
def h(session_factory: sessionmaker[Session]) -> Harness:
    auth = DefaultAuthorizationProvider()
    clock = SystemClock()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    def make(cls, **extra):  # type: ignore[no-untyped-def]
        return cls(uow_factory=uow_factory, authorization=auth, clock=clock, **extra)

    return Harness(
        session_factory=session_factory,
        register=make(RegisterAgent),
        open_approval=make(OpenApprovalRequest),
        decide=make(DecideApprovalRequest),
    )


def reload(h: Harness, request: ApprovalRequest) -> ApprovalRequest:
    with h.uow() as uow:
        stored = uow.approvals.get(request.id)
        assert stored is not None
        return stored


class TestTheControlCanBeCompleted:
    def test_a_second_authorized_human_completes_the_control(self, h: Harness) -> None:
        """The regression: a two-person approval now reaches APPROVED.

        Before the fix this raised ``ResourceNotFound`` for BOB and the request
        sat pending forever. The owner's signature alone must leave it pending,
        and the second signature must flip it to approved with both humans
        recorded.
        """
        alice, bob = principal_for(ALICE), principal_for(BOB)
        h.tenant((ALICE, OrganizationRole.ORG_MEMBER), (BOB, OrganizationRole.ORG_ADMIN))
        agent = h.agent(alice, SENSITIVE)

        half = h.two_person_request(alice, agent)
        assert half.status is ApprovalStatus.PENDING
        assert half.decided_by_user_id == ALICE
        assert half.second_approver_user_id is None
        assert half.approvals_recorded == 1

        completed = h.decide.run(bob, approval_id=str(half.id), approve=True, reason="ok")

        assert completed.status is ApprovalStatus.APPROVED
        assert completed.decided_by_user_id == ALICE
        assert completed.second_approver_user_id == BOB
        assert completed.approvals_recorded == 2
        assert completed.is_usable(now=completed.requested_at) is True

    def test_an_org_owner_can_also_second_sign(self, h: Harness) -> None:
        alice, bob = principal_for(ALICE), principal_for(BOB)
        h.tenant((ALICE, OrganizationRole.ORG_MEMBER), (BOB, OrganizationRole.ORG_OWNER))
        agent = h.agent(alice, SENSITIVE)
        half = h.two_person_request(alice, agent)

        assert (
            h.decide.run(bob, approval_id=str(half.id), approve=True).status
            is ApprovalStatus.APPROVED
        )

    def test_an_independent_approver_can_refuse(self, h: Harness) -> None:
        """A refusal must be possible too, and must preserve the first grant.

        The aggregate models "half-approved then refused" as a distinct outcome;
        the tenant approver is the only human who can reach it, so denying must
        be admitted alongside approving.
        """
        alice, bob = principal_for(ALICE), principal_for(BOB)
        h.tenant((ALICE, OrganizationRole.ORG_MEMBER), (BOB, OrganizationRole.ORG_ADMIN))
        agent = h.agent(alice, SENSITIVE)
        half = h.two_person_request(alice, agent)

        denied = h.decide.run(bob, approval_id=str(half.id), approve=False, reason="unsafe")

        assert denied.status is ApprovalStatus.DENIED
        assert denied.denied_by_user_id == BOB
        # The first human's grant is not erased: an auditor needs both records.
        assert denied.decided_by_user_id == ALICE
        assert denied.is_usable(now=denied.requested_at) is False


class TestTheNewAuthorityIsBounded:
    def test_the_second_signer_cannot_open_the_first_signature(self, h: Harness) -> None:
        """An org admin cannot sign a request nobody has signed.

        The first signature belongs to the owner, where responsibility for the
        agent was taken on. Letting an admin supply it would let them start a
        control the owner never entered.
        """
        alice, bob = principal_for(ALICE), principal_for(BOB)
        h.tenant((ALICE, OrganizationRole.ORG_MEMBER), (BOB, OrganizationRole.ORG_ADMIN))
        agent = h.agent(alice, SENSITIVE)
        request = h.open_approval.run(
            alice, agent_id=str(agent.id), capability=SENSITIVE.value, resource_ref="note:all"
        )
        with h.uow() as uow:
            stored = uow.approvals.get(request.id)
            assert stored is not None
            stored.required_approvals = 2
            uow.approvals.save(stored)
            uow.commit()

        with pytest.raises(ResourceNotFound):
            h.decide.run(bob, approval_id=str(request.id), approve=True)

    def test_a_single_approval_request_is_owner_only(self, h: Harness) -> None:
        """The tenant approver has no standing on an ordinary one-person request."""
        alice, bob = principal_for(ALICE), principal_for(BOB)
        h.tenant((ALICE, OrganizationRole.ORG_MEMBER), (BOB, OrganizationRole.ORG_ADMIN))
        agent = h.agent(alice, SENSITIVE)
        request = h.open_approval.run(
            alice, agent_id=str(agent.id), capability=SENSITIVE.value, resource_ref="note:all"
        )

        with pytest.raises(ResourceNotFound):
            h.decide.run(bob, approval_id=str(request.id), approve=True)

    def test_the_same_human_cannot_supply_both_signatures(self, h: Harness) -> None:
        """Separation of duties survives the wider authority.

        ALICE owns the agent *and* is an org admin. Her second click must not
        count as a second approver — that is the whole point of the control.

        She is *authorized* to decide (she owns the agent), so the refusal comes
        from the aggregate's distinctness rule rather than the 404 path. That
        split is the point: authorization says she may decide; the aggregate says
        she may not be both signatories.
        """
        alice = principal_for(ALICE)
        h.tenant((ALICE, OrganizationRole.ORG_OWNER))
        agent = h.agent(alice, SENSITIVE)
        half = h.two_person_request(alice, agent)

        with pytest.raises(InvariantViolation):
            h.decide.run(alice, approval_id=str(half.id), approve=True)

        assert reload(h, half).status is ApprovalStatus.PENDING
        assert reload(h, half).approvals_recorded == 1

    @pytest.mark.parametrize(
        "role",
        [
            OrganizationRole.ORG_SECURITY_ADMIN,
            OrganizationRole.ORG_AUDITOR,
            OrganizationRole.ORG_MEMBER,
        ],
    )
    def test_read_only_roles_cannot_second_sign(self, h: Harness, role: OrganizationRole) -> None:
        """Observation roles are not approvers.

        ``ORG_SECURITY_ADMIN`` and ``ORG_AUDITOR`` are deliberately read-only
        (pinned in ``tests/domain/test_organization.py``); granting them the
        co-signing capability would collapse the separation the role exists for.
        """
        alice, bob = principal_for(ALICE), principal_for(BOB)
        h.tenant((ALICE, OrganizationRole.ORG_MEMBER), (BOB, role))
        agent = h.agent(alice, SENSITIVE)
        half = h.two_person_request(alice, agent)

        with pytest.raises(ResourceNotFound):
            h.decide.run(bob, approval_id=str(half.id), approve=True)

    def test_an_admin_in_an_unrelated_tenant_cannot_second_sign(self, h: Harness) -> None:
        """The authority is bounded by a shared tenant.

        BOB is an admin of a *different* organization than ALICE. They share no
        tenant to be jointly responsible within, so BOB has no standing.
        """
        alice, bob = principal_for(ALICE), principal_for(BOB)
        h.tenant((ALICE, OrganizationRole.ORG_MEMBER))
        h.tenant((BOB, OrganizationRole.ORG_ADMIN))
        agent = h.agent(alice, SENSITIVE)
        half = h.two_person_request(alice, agent)

        with pytest.raises(ResourceNotFound):
            h.decide.run(bob, approval_id=str(half.id), approve=True)

    def test_an_admin_cannot_second_sign_in_a_suspended_tenant(self, h: Harness) -> None:
        """A suspended tenant revokes the authority with everything else in it."""
        alice, bob = principal_for(ALICE), principal_for(BOB)
        h.tenant((ALICE, OrganizationRole.ORG_MEMBER), (BOB, OrganizationRole.ORG_ADMIN))
        agent = h.agent(alice, SENSITIVE)
        half = h.two_person_request(alice, agent)

        with h.uow() as uow:
            organization = uow.organization_memberships.list_for_user(BOB)[0]
            org = uow.organizations.get(organization.organization_id)
            assert org is not None
            org.change_status(status=OrganizationStatus.SUSPENDED)
            uow.organizations.save(org)
            uow.commit()

        with pytest.raises(ResourceNotFound):
            h.decide.run(bob, approval_id=str(half.id), approve=True)
