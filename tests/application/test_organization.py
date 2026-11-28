"""Organization persistence and use-case integration tests.

These run the real SQLAlchemy adapters against a real (SQLite) database through
the unit of work. Nothing is mocked: the point is to prove the whole vertical
slice — domain, ports, adapters, UoW, authorization policy, use cases — works
together, including the security invariants.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import AccessDenied, InvalidInput, ResourceNotFound
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.organization import (
    AddTeamMember,
    ChangeMemberRole,
    CreateOrganization,
    CreateOrganizationUnit,
    CreateTeam,
    DeprovisionMember,
    GetOrganization,
    InviteOrganizationMember,
    ListMyOrganizations,
    ListOrganizationMembers,
    ListOrganizationUnits,
    ListTeamMembers,
    MoveOrganizationUnit,
    SetMemberManager,
    SuspendMember,
    UpdateOrganization,
)
from notes_service.application.use_cases.organization_policy import (
    DefaultOrganizationAuthorizationProvider,
)
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.domain.organization.value_objects import (
    MembershipStatus,
    OrganizationId,
    OrganizationRole,
    OrganizationStatus,
    OrganizationUnitId,
    OrgPermission,
    OrgUnitType,
    TeamRole,
)
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.security.clock import SystemClock

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
BOB = UserId(DEMO_USERS[1].id)
CAROL = UserId(DEMO_USERS[2].id)


def principal_for(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email(f"{str(user_id)[:8]}@example.com"),
        display_name="X",
        provider="demo",
        subject=str(user_id),
    )


class Harness:
    """Builds organization use cases over one session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._session_factory)

    def make(self, cls):  # type: ignore[no-untyped-def]
        return cls(
            uow_factory=self.uow,
            authorization=DefaultAuthorizationProvider(),
            organization_authorization=DefaultOrganizationAuthorizationProvider(),
            clock=SystemClock(),
        )


@pytest.fixture
def harness(session_factory: sessionmaker[Session]) -> Harness:
    return Harness(session_factory)


@pytest.fixture
def org(harness: Harness):  # type: ignore[no-untyped-def]
    """An organization owned by Alice, with Bob as a plain member."""
    created = harness.make(CreateOrganization).run(
        principal_for(ALICE), name="Acme Corp", slug="acme"
    )
    harness.make(InviteOrganizationMember).run(
        principal_for(ALICE),
        organization_id=created.organization.id,
        user_id=BOB,
        role=OrganizationRole.ORG_MEMBER,
    )
    return created


class TestCreateOrganization:
    def test_creator_becomes_owner(self, harness: Harness) -> None:
        view = harness.make(CreateOrganization).run(principal_for(ALICE), name="Acme", slug="acme")
        assert view.organization.slug == "acme"
        assert view.membership.role is OrganizationRole.ORG_OWNER
        assert view.membership.user_id == str(ALICE)
        assert view.capabilities == frozenset(OrgPermission)

    def test_slug_must_be_unique(self, harness: Harness) -> None:
        harness.make(CreateOrganization).run(principal_for(ALICE), name="Acme", slug="acme")
        with pytest.raises(InvalidInput):
            harness.make(CreateOrganization).run(principal_for(BOB), name="Acme Two", slug="acme")

    def test_invalid_slug_rejected(self, harness: Harness) -> None:
        from notes_service.domain.errors import ValidationError

        with pytest.raises(ValidationError):
            harness.make(CreateOrganization).run(
                principal_for(ALICE), name="Acme", slug="Not A Slug"
            )

    def test_persisted_and_listed(self, harness: Harness) -> None:
        harness.make(CreateOrganization).run(principal_for(ALICE), name="Acme", slug="acme")
        mine = harness.make(ListMyOrganizations).run(principal_for(ALICE))
        assert [v.organization.slug for v in mine] == ["acme"]
        # Bob is not a member yet, so he sees nothing.
        assert harness.make(ListMyOrganizations).run(principal_for(BOB)) == []


class TestTenantIsolation:
    def test_non_member_gets_not_found(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        """A non-member must not be able to distinguish 'exists' from 'denied'."""
        with pytest.raises(ResourceNotFound):
            harness.make(GetOrganization).run(
                principal_for(CAROL), organization_id=org.organization.id
            )

    def test_unknown_organization_gets_not_found(self, harness: Harness) -> None:
        with pytest.raises(ResourceNotFound):
            harness.make(GetOrganization).run(
                principal_for(ALICE), organization_id=OrganizationId.new()
            )

    def test_member_without_capability_gets_forbidden(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        """A known member asking for something above their role gets 403, not 404."""
        with pytest.raises(AccessDenied):
            harness.make(CreateOrganizationUnit).run(
                principal_for(BOB),
                organization_id=org.organization.id,
                name="Engineering",
                slug="engineering",
            )

    def test_suspended_membership_loses_all_authority(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        members = harness.make(ListOrganizationMembers).run(
            principal_for(ALICE), organization_id=org.organization.id
        )
        bob_membership = next(m for m in members.items if m.user_id == str(BOB))
        harness.make(SuspendMember).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            membership_id=bob_membership.id,
        )
        with pytest.raises(ResourceNotFound):
            harness.make(GetOrganization).run(
                principal_for(BOB), organization_id=org.organization.id
            )

    def test_suspended_organization_grants_nothing(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        harness.make(UpdateOrganization).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            status=OrganizationStatus.SUSPENDED.value,
        )
        # Even the owner can no longer act; the tenant is off.
        with pytest.raises(ResourceNotFound):
            harness.make(GetOrganization).run(
                principal_for(ALICE), organization_id=org.organization.id
            )


class TestUnits:
    def test_create_root_unit(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        unit = harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Engineering",
            slug="engineering",
            unit_type=OrgUnitType.DIVISION,
        )
        assert unit.parent_id is None
        assert unit.unit_type is OrgUnitType.DIVISION

    def test_nested_unit(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        parent = harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Engineering",
            slug="engineering",
        )
        child = harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Platform",
            slug="platform",
            parent_id=parent.id,
        )
        assert child.parent_id == parent.id
        units = harness.make(ListOrganizationUnits).run(
            principal_for(ALICE), organization_id=org.organization.id
        )
        assert {u.slug for u in units} == {"engineering", "platform"}

    def test_duplicate_unit_slug_rejected(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Engineering",
            slug="engineering",
        )
        with pytest.raises(InvalidInput):
            harness.make(CreateOrganizationUnit).run(
                principal_for(ALICE),
                organization_id=org.organization.id,
                name="Engineering Two",
                slug="engineering",
            )

    def test_cannot_reparent_under_own_child(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        """The cycle guard must hold through the real ancestor-path lookup."""
        from notes_service.domain.errors import InvariantViolation

        parent = harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Engineering",
            slug="engineering",
        )
        child = harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Platform",
            slug="platform",
            parent_id=parent.id,
        )
        with pytest.raises(InvariantViolation):
            harness.make(MoveOrganizationUnit).run(
                principal_for(ALICE),
                organization_id=org.organization.id,
                unit_id=parent.id,
                parent_id=child.id,
            )

    def test_cannot_attach_parent_from_other_organization(self, harness: Harness) -> None:
        first = harness.make(CreateOrganization).run(
            principal_for(ALICE), name="First", slug="first"
        )
        second = harness.make(CreateOrganization).run(
            principal_for(BOB), name="Second", slug="second"
        )
        foreign_unit = harness.make(CreateOrganizationUnit).run(
            principal_for(BOB),
            organization_id=second.organization.id,
            name="Foreign",
            slug="foreign",
        )
        with pytest.raises(InvalidInput):
            harness.make(CreateOrganizationUnit).run(
                principal_for(ALICE),
                organization_id=first.organization.id,
                name="Engineering",
                slug="engineering",
                parent_id=foreign_unit.id,
            )


class TestTeams:
    def test_create_team_in_unit(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        unit = harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Engineering",
            slug="engineering",
        )
        team = harness.make(CreateTeam).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            unit_id=unit.id,
            name="Platform",
            slug="platform",
        )
        assert team.unit_id == unit.id

    def test_team_member_must_be_org_member(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        """Team membership must never be a back door into the tenant."""
        unit = harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Engineering",
            slug="engineering",
        )
        team = harness.make(CreateTeam).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            unit_id=unit.id,
            name="Platform",
            slug="platform",
        )
        with pytest.raises(InvalidInput):
            harness.make(AddTeamMember).run(
                principal_for(ALICE),
                organization_id=org.organization.id,
                team_id=team.id,
                user_id=CAROL,  # not an org member
            )

    def test_add_and_list_team_members(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        unit = harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Engineering",
            slug="engineering",
        )
        team = harness.make(CreateTeam).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            unit_id=unit.id,
            name="Platform",
            slug="platform",
        )
        harness.make(AddTeamMember).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            team_id=team.id,
            user_id=BOB,
            role=TeamRole.TEAM_LEAD,
        )
        members = harness.make(ListTeamMembers).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            team_id=team.id,
        )
        assert [(m.user_id, m.role) for m in members] == [(str(BOB), TeamRole.TEAM_LEAD)]


class TestMembershipAndRoles:
    def test_cannot_invite_unknown_user(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(InvalidInput):
            harness.make(InviteOrganizationMember).run(
                principal_for(ALICE),
                organization_id=org.organization.id,
                user_id=UserId.new(),
            )

    def test_cannot_invite_existing_member(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        with pytest.raises(InvalidInput):
            harness.make(InviteOrganizationMember).run(
                principal_for(ALICE),
                organization_id=org.organization.id,
                user_id=BOB,
            )

    def test_admin_cannot_grant_owner_role(self, harness: Harness) -> None:
        """Privilege escalation guard: you may not grant above your own rank."""
        created = harness.make(CreateOrganization).run(
            principal_for(ALICE), name="Acme", slug="acme"
        )
        harness.make(InviteOrganizationMember).run(
            principal_for(ALICE),
            organization_id=created.organization.id,
            user_id=BOB,
            role=OrganizationRole.ORG_ADMIN,
        )
        with pytest.raises(AccessDenied):
            harness.make(InviteOrganizationMember).run(
                principal_for(BOB),
                organization_id=created.organization.id,
                user_id=CAROL,
                role=OrganizationRole.ORG_OWNER,
            )

    def test_last_admin_cannot_be_demoted(self, harness: Harness) -> None:
        created = harness.make(CreateOrganization).run(
            principal_for(ALICE), name="Acme", slug="acme"
        )
        with pytest.raises(InvalidInput):
            harness.make(ChangeMemberRole).run(
                principal_for(ALICE),
                organization_id=created.organization.id,
                membership_id=created.membership.id,
                role=OrganizationRole.ORG_MEMBER,
            )

    def test_admin_can_be_demoted_when_another_remains(self, harness: Harness) -> None:
        created = harness.make(CreateOrganization).run(
            principal_for(ALICE), name="Acme", slug="acme"
        )
        bob = harness.make(InviteOrganizationMember).run(
            principal_for(ALICE),
            organization_id=created.organization.id,
            user_id=BOB,
            role=OrganizationRole.ORG_ADMIN,
        )
        demoted = harness.make(ChangeMemberRole).run(
            principal_for(ALICE),
            organization_id=created.organization.id,
            membership_id=bob.id,
            role=OrganizationRole.ORG_MEMBER,
        )
        assert demoted.role is OrganizationRole.ORG_MEMBER

    def test_member_cannot_change_roles(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        with pytest.raises(AccessDenied):
            harness.make(ChangeMemberRole).run(
                principal_for(BOB),
                organization_id=org.organization.id,
                membership_id=org.membership.id,
                role=OrganizationRole.ORG_MEMBER,
            )

    def test_last_admin_cannot_be_deprovisioned(self, harness: Harness) -> None:
        created = harness.make(CreateOrganization).run(
            principal_for(ALICE), name="Acme", slug="acme"
        )
        with pytest.raises(InvalidInput):
            harness.make(DeprovisionMember).run(
                principal_for(ALICE),
                organization_id=created.organization.id,
                membership_id=created.membership.id,
            )


class TestReportingLine:
    def test_set_manager(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        members = harness.make(ListOrganizationMembers).run(
            principal_for(ALICE), organization_id=org.organization.id
        )
        bob_membership = next(m for m in members.items if m.user_id == str(BOB))
        updated = harness.make(SetMemberManager).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            membership_id=bob_membership.id,
            manager_user_id=ALICE,
        )
        assert updated.manager_user_id == str(ALICE)

    def test_manager_must_be_member(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        members = harness.make(ListOrganizationMembers).run(
            principal_for(ALICE), organization_id=org.organization.id
        )
        bob_membership = next(m for m in members.items if m.user_id == str(BOB))
        with pytest.raises(InvalidInput):
            harness.make(SetMemberManager).run(
                principal_for(ALICE),
                organization_id=org.organization.id,
                membership_id=bob_membership.id,
                manager_user_id=CAROL,  # not a member
            )

    def test_reporting_cycle_rejected(self, harness: Harness) -> None:
        """A -> B -> A must be impossible even when set one edge at a time."""
        created = harness.make(CreateOrganization).run(
            principal_for(ALICE), name="Acme", slug="acme"
        )
        alice_membership = created.membership
        bob_membership = harness.make(InviteOrganizationMember).run(
            principal_for(ALICE),
            organization_id=created.organization.id,
            user_id=BOB,
        )
        harness.make(SetMemberManager).run(
            principal_for(ALICE),
            organization_id=created.organization.id,
            membership_id=bob_membership.id,
            manager_user_id=ALICE,
        )
        # Now try to make Alice report to Bob: the chain Alice -> Bob -> Alice.
        with pytest.raises(InvalidInput):
            harness.make(SetMemberManager).run(
                principal_for(ALICE),
                organization_id=created.organization.id,
                membership_id=alice_membership.id,
                manager_user_id=BOB,
            )


class TestPersistenceConstraints:
    def test_duplicate_org_slug_blocked_at_database_level(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        """The unique constraint is the real guarantee; the use case check is UX."""
        from notes_service.domain.organization.entities import Organization

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.organizations.add(Organization.create(slug="dup", name="Dup"))
            uow.commit()
        with pytest.raises(IntegrityError), SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.organizations.add(Organization.create(slug="dup", name="Dup Two"))
            uow.commit()

    def test_organization_survives_round_trip(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        fetched = harness.make(GetOrganization).run(
            principal_for(ALICE), organization_id=org.organization.id
        )
        assert fetched.organization.name == "Acme Corp"
        assert fetched.membership.role is OrganizationRole.ORG_OWNER
        assert fetched.membership.status is MembershipStatus.ACTIVE

    def test_update_bumps_version(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        updated = harness.make(UpdateOrganization).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Acme Holdings",
        )
        assert updated.organization.name == "Acme Holdings"
        assert updated.organization.version == 2

    def test_optimistic_concurrency_conflict(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        from notes_service.domain.errors import VersionConflict

        with pytest.raises(VersionConflict):
            harness.make(UpdateOrganization).run(
                principal_for(ALICE),
                organization_id=org.organization.id,
                name="Stale Write",
                expected_version=99,
            )

    def test_unit_ancestor_path(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        """The ancestor path drives cycle prevention; verify it is real."""
        root = harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Root",
            slug="root",
        )
        mid = harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Mid",
            slug="mid",
            parent_id=root.id,
        )
        leaf = harness.make(CreateOrganizationUnit).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            name="Leaf",
            slug="leaf",
            parent_id=mid.id,
        )
        with harness.uow() as uow:
            path = uow.organization_units.ancestor_path(leaf.id)
        assert path == [root.id, mid.id]
        assert OrganizationUnitId(str(mid.id)) in path


class TestSuspendedTenantIsNotListed:
    """A suspended tenant must not be listed as one the caller can act in.

    ``GetOrganization`` refuses a suspended tenant (404) and the domain tenant
    rule treats a non-operational organization as no access at all, so the
    listing must agree. Otherwise the list tells the client "you have these
    capabilities here" and every other call refuses it.
    """

    def test_suspended_organization_is_omitted_from_my_organizations(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        harness.make(UpdateOrganization).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            status=OrganizationStatus.SUSPENDED.value,
        )

        assert harness.make(ListMyOrganizations).run(principal_for(ALICE)) == []
        assert harness.make(ListMyOrganizations).run(principal_for(BOB)) == []

    def test_other_tenants_survive_one_tenant_being_suspended(
        self,
        harness: Harness,
        org,  # type: ignore[no-untyped-def]
    ) -> None:
        """Suspending one tenant must not hide the caller's remaining tenants."""
        other = harness.make(CreateOrganization).run(
            principal_for(ALICE), name="Globex", slug="globex"
        )
        harness.make(UpdateOrganization).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            status=OrganizationStatus.SUSPENDED.value,
        )

        mine = harness.make(ListMyOrganizations).run(principal_for(ALICE))
        assert [v.organization.id for v in mine] == [other.organization.id]
