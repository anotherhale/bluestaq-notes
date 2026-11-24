"""Domain tests for the organization hierarchy.

These are pure unit tests: no database, no framework. They pin the invariants
that the persistence and application layers rely on, so a regression here is
caught before it can reach a database.
"""

from __future__ import annotations

import pytest

from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.organization.entities import (
    Organization,
    OrganizationMembership,
    OrganizationUnit,
    Team,
    TeamMembership,
)
from notes_service.domain.organization.value_objects import (
    MembershipStatus,
    OrganizationRole,
    OrganizationStatus,
    OrganizationUnitId,
    OrgPermission,
    OrgUnitType,
    Slug,
    TeamRole,
    UnitStatus,
    capabilities_for,
)


class TestSlug:
    @pytest.mark.parametrize("value", ["acme", "acme-corp", "a1", "x" * 63])
    def test_accepts_valid_slugs(self, value: str) -> None:
        assert Slug(value) == value

    @pytest.mark.parametrize(
        "value",
        ["", "Acme", "acme_corp", "-acme", "acme-", "ac me", "x" * 64, "acme--corp"],
    )
    def test_rejects_invalid_slugs(self, value: str) -> None:
        with pytest.raises(ValidationError):
            Slug(value)


class TestOrganization:
    def test_create_defaults_to_active(self) -> None:
        org = Organization.create(slug="acme", name="Acme Corp")
        assert org.status is OrganizationStatus.ACTIVE
        assert org.is_operational is True
        assert org.version == 1

    def test_rename_bumps_version(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        org.rename(name="Acme Holdings")
        assert org.name == "Acme Holdings"
        assert org.version == 2

    @pytest.mark.parametrize("status", [OrganizationStatus.SUSPENDED, OrganizationStatus.ARCHIVED])
    def test_suspended_or_archived_is_not_operational(self, status: OrganizationStatus) -> None:
        org = Organization.create(slug="acme", name="Acme", status=status)
        assert org.is_operational is False

    def test_archived_organization_cannot_change_status(self) -> None:
        org = Organization.create(slug="acme", name="Acme", status=OrganizationStatus.ARCHIVED)
        with pytest.raises(InvariantViolation):
            org.change_status(status=OrganizationStatus.ACTIVE)

    def test_change_status_is_idempotent(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        org.change_status(status=OrganizationStatus.ACTIVE)
        assert org.version == 1


class TestOrganizationUnit:
    def _org(self) -> Organization:
        return Organization.create(slug="acme", name="Acme")

    def test_root_unit(self) -> None:
        org = self._org()
        unit = OrganizationUnit.create(
            organization_id=org.id, name="Engineering", slug="engineering"
        )
        assert unit.parent_id is None
        assert unit.unit_type is OrgUnitType.DEPARTMENT
        assert unit.is_operational is True

    def test_cannot_be_own_parent(self) -> None:
        org = self._org()
        unit = OrganizationUnit.create(
            organization_id=org.id, name="Engineering", slug="engineering"
        )
        with pytest.raises(InvariantViolation):
            unit.assert_can_attach_to(parent_id=unit.id, parent_path=[])

    def test_cannot_move_under_own_descendant(self) -> None:
        """A cycle must be rejected using the ancestor path from the repository."""
        org = self._org()
        parent = OrganizationUnit.create(
            organization_id=org.id, name="Engineering", slug="engineering"
        )
        child = OrganizationUnit.create(
            organization_id=org.id,
            name="Platform",
            slug="platform",
            parent_id=parent.id,
        )
        # Attempt to make the parent a child of its own child: the child's
        # ancestor path contains the parent, which is legal, but moving the
        # *parent* under the child means the child is in the parent's new
        # ancestor chain.
        with pytest.raises(InvariantViolation):
            parent.assert_can_attach_to(parent_id=child.id, parent_path=[parent.id])

    def test_depth_limit(self) -> None:
        org = self._org()
        unit = OrganizationUnit.create(organization_id=org.id, name="Deep", slug="deep")
        # A path at the maximum depth means adding one more level overflows.
        deep_path = [OrganizationUnitId.new() for _ in range(32)]
        with pytest.raises(InvariantViolation):
            unit.assert_can_attach_to(parent_id=deep_path[-1], parent_path=deep_path)

    def test_move_to_root_always_allowed(self) -> None:
        org = self._org()
        unit = OrganizationUnit.create(
            organization_id=org.id, name="Engineering", slug="engineering"
        )
        unit.move_to(parent_id=None, parent_path=[])
        assert unit.parent_id is None

    def test_archive_and_reactivate(self) -> None:
        org = self._org()
        unit = OrganizationUnit.create(
            organization_id=org.id, name="Engineering", slug="engineering"
        )
        unit.archive()
        assert unit.status is UnitStatus.ARCHIVED
        assert unit.is_operational is False
        unit.reactivate()
        assert unit.is_operational is True


class TestTeam:
    def test_create_requires_unit(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        unit = OrganizationUnit.create(
            organization_id=org.id, name="Engineering", slug="engineering"
        )
        team = Team.create(
            organization_id=org.id, unit_id=unit.id, name="Platform", slug="platform"
        )
        assert team.unit_id == unit.id
        assert team.parent_team_id is None

    def test_cannot_be_own_parent(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        unit = OrganizationUnit.create(
            organization_id=org.id, name="Engineering", slug="engineering"
        )
        team = Team.create(
            organization_id=org.id, unit_id=unit.id, name="Platform", slug="platform"
        )
        with pytest.raises(InvariantViolation):
            team.set_parent(parent_team_id=team.id, parent_path=[])

    def test_cannot_create_team_cycle(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        unit = OrganizationUnit.create(
            organization_id=org.id, name="Engineering", slug="engineering"
        )
        parent = Team.create(
            organization_id=org.id, unit_id=unit.id, name="Platform", slug="platform"
        )
        child = Team.create(
            organization_id=org.id,
            unit_id=unit.id,
            name="Runtime",
            slug="runtime",
            parent_team_id=parent.id,
        )
        with pytest.raises(InvariantViolation):
            parent.set_parent(parent_team_id=child.id, parent_path=[parent.id])


class TestOrganizationMembership:
    def test_grant_defaults_to_member(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        membership = OrganizationMembership.grant(organization_id=org.id, user_id="user-1")
        assert membership.role is OrganizationRole.ORG_MEMBER
        assert membership.status is MembershipStatus.ACTIVE
        assert membership.grants_access is True

    def test_cannot_be_own_manager(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        with pytest.raises(InvariantViolation):
            OrganizationMembership.grant(
                organization_id=org.id, user_id="user-1", manager_user_id="user-1"
            )

    def test_title_is_trimmed_and_length_checked(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        membership = OrganizationMembership.grant(
            organization_id=org.id, user_id="user-1", title="  CTO  "
        )
        assert membership.title == "CTO"
        with pytest.raises(ValidationError):
            OrganizationMembership.grant(organization_id=org.id, user_id="user-2", title="x" * 121)

    def test_blank_title_becomes_none(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        membership = OrganizationMembership.grant(
            organization_id=org.id, user_id="user-1", title="   "
        )
        assert membership.title is None

    def test_suspend_removes_access_but_is_reversible(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        membership = OrganizationMembership.grant(organization_id=org.id, user_id="user-1")
        membership.suspend()
        assert membership.grants_access is False
        membership.reactivate()
        assert membership.grants_access is True

    def test_deprovision_is_terminal_and_clears_manager(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        membership = OrganizationMembership.grant(
            organization_id=org.id, user_id="user-1", manager_user_id="boss"
        )
        membership.deprovision()
        assert membership.grants_access is False
        assert membership.manager_user_id is None
        assert membership.ended_at is not None
        with pytest.raises(InvariantViolation):
            membership.reactivate()
        with pytest.raises(InvariantViolation):
            membership.suspend()

    def test_cannot_change_role_of_inactive_membership(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        membership = OrganizationMembership.grant(organization_id=org.id, user_id="user-1")
        membership.suspend()
        with pytest.raises(InvariantViolation):
            membership.change_role(role=OrganizationRole.ORG_ADMIN)


class TestTeamMembership:
    def test_grant_and_remove(self) -> None:
        org = Organization.create(slug="acme", name="Acme")
        unit = OrganizationUnit.create(
            organization_id=org.id, name="Engineering", slug="engineering"
        )
        team = Team.create(
            organization_id=org.id, unit_id=unit.id, name="Platform", slug="platform"
        )
        membership = TeamMembership.grant(team_id=team.id, user_id="user-1")
        assert membership.role is TeamRole.TEAM_MEMBER
        assert membership.grants_access is True
        membership.remove()
        assert membership.grants_access is False


class TestCapabilities:
    def test_owner_has_every_capability(self) -> None:
        assert capabilities_for(OrganizationRole.ORG_OWNER) == frozenset(OrgPermission)

    def test_member_can_only_view(self) -> None:
        assert capabilities_for(OrganizationRole.ORG_MEMBER) == {OrgPermission.VIEW_ORGANIZATION}

    def test_auditor_reads_audit_but_cannot_manage(self) -> None:
        caps = capabilities_for(OrganizationRole.ORG_AUDITOR)
        assert OrgPermission.VIEW_AUDIT in caps
        assert OrgPermission.MANAGE_MEMBERSHIPS not in caps
        assert OrgPermission.MANAGE_ROLES not in caps

    def test_admin_cannot_manage_roles_of_owner_tier(self) -> None:
        """Role rank ordering must place owner strictly above admin."""
        assert OrganizationRole.ORG_OWNER.at_least(OrganizationRole.ORG_ADMIN)
        assert not OrganizationRole.ORG_ADMIN.at_least(OrganizationRole.ORG_OWNER)

    def test_security_admin_is_read_only_over_structure(self) -> None:
        """Separation of duties: security admin observes, it does not administer.

        This is deliberate. If ORG_SECURITY_ADMIN could manage memberships or
        roles it could grant itself access, collapsing the distinction between
        the auditor and the administered.
        """
        assert OrganizationRole.ORG_SECURITY_ADMIN.is_administrative is False
        assert capabilities_for(OrganizationRole.ORG_SECURITY_ADMIN) == {
            OrgPermission.VIEW_ORGANIZATION,
            OrgPermission.VIEW_AUDIT,
        }

    def test_only_owner_and_admin_are_administrative(self) -> None:
        administrative = {role for role in OrganizationRole if role.is_administrative}
        assert administrative == {
            OrganizationRole.ORG_OWNER,
            OrganizationRole.ORG_ADMIN,
        }
