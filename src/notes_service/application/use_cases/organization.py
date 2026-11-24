"""Organization administration use cases.

Every use case here follows the same shape:

1. resolve the caller's membership in the target organization;
2. ask the :class:`OrganizationAuthorizationProvider` for an explicit decision on
   the capability the operation needs;
3. perform the domain operation; 4. commit through the unit of work.

Two conventions are load-bearing and easy to get wrong:

* **404 vs 403.** A caller with *no* membership gets ``ResourceNotFound`` so the
  API cannot be used to enumerate other tenants' organizations. A caller who is
  a member but lacks the capability gets ``AccessDenied`` (403), because
  revealing "you are not allowed" to a known member leaks nothing.
* **The last administrator cannot be removed.** Demoting or deprovisioning the
  final active admin would strand the organization with nobody able to manage
  it, so that is rejected as an ``InvalidInput``.
"""

from __future__ import annotations

from dataclasses import dataclass

from notes_service.application.errors import AccessDenied, InvalidInput, ResourceNotFound
from notes_service.application.ports.organization_authorization import (
    OrganizationAuthorizationProvider,
)
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.application.use_cases.base import UseCase
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.organization.entities import (
    Organization,
    OrganizationMembership,
    OrganizationUnit,
    Team,
    TeamMembership,
)
from notes_service.domain.organization.value_objects import (
    MembershipStatus,
    OrganizationId,
    OrganizationMembershipId,
    OrganizationRole,
    OrganizationStatus,
    OrganizationUnitId,
    OrgPermission,
    OrgUnitType,
    Slug,
    TeamId,
    TeamRole,
    capabilities_for,
)

# Policy denials that mean the caller has no standing in this tenant at all.
# These are surfaced as 404 rather than 403 so that a caller cannot distinguish
# "you are not a member" from "this organization does not exist" (ADR-008).
_NO_STANDING_DENIALS = frozenset(
    {
        "organization_not_operational",
        "not_a_member",
        "membership_suspended",
        "membership_deprovisioned",
        "membership_wrong_organization",
    }
)


class OrganizationUseCase(UseCase):
    """Base class adding organization authorization helpers.

    Accepts the organization authorization provider in addition to the shared
    dependencies, so the two policies stay independent and separately
    replaceable.
    """

    def __init__(
        self,
        *,
        organization_authorization: OrganizationAuthorizationProvider,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._org_authorization = organization_authorization

    def _require_permission(
        self,
        *,
        organization: Organization,
        membership: OrganizationMembership | None,
        permission: OrgPermission,
    ) -> OrganizationMembership:
        """Return the membership when allowed; raise 404/403 otherwise.

        ``membership is None`` means the caller is not a member at all, which
        must be indistinguishable from "organization does not exist".
        """
        if membership is None:
            raise ResourceNotFound("organization not found")
        decision = self._org_authorization.decide(
            organization=organization, membership=membership, permission=permission
        )
        if not decision.allowed:
            # Denials that mean "you have no standing here at all" are reported
            # as 404 so a caller cannot probe for the existence of a tenant they
            # cannot see. Only a caller with real standing who merely lacks this
            # capability gets a 403 (ADR-008).
            if decision.reason in _NO_STANDING_DENIALS:
                raise ResourceNotFound("organization not found")
            raise AccessDenied("not permitted to perform this organization action")
        return membership

    def _load_organization(
        self, uow: UnitOfWork, *, organization_id: OrganizationId, principal: Principal
    ) -> tuple[Organization, OrganizationMembership | None]:
        """Load the organization and the caller's membership, or 404.

        The membership lookup is scoped by organization id, so a membership in a
        different organization can never be returned here.
        """
        organization = uow.organizations.get(organization_id)
        if organization is None:
            raise ResourceNotFound("organization not found")
        membership = uow.organization_memberships.get_for_user(organization_id, principal.user_id)
        return organization, membership

    @staticmethod
    def _assert_not_last_admin(
        uow: UnitOfWork, organization_id: OrganizationId, target: OrganizationMembership
    ) -> None:
        """Refuse an operation that would leave the organization with no admin.

        Any path that removes administrative authority (demote, suspend,
        deprovision) must funnel through this, otherwise an organization can be
        stranded with no one able to administer it.
        """
        if (
            target.role.is_administrative
            and target.grants_access
            and uow.organization_memberships.count_active_admins(organization_id) <= 1
        ):
            raise InvalidInput("cannot remove the last administrator of the organization")


# -- read models -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrganizationView:
    """Read model returned to the API for one organization."""

    organization: Organization
    membership: OrganizationMembership
    capabilities: frozenset[OrgPermission]


# -- organization lifecycle ------------------------------------------------


class CreateOrganization(OrganizationUseCase):
    """Create a new organization and make the caller its owner.

    Bootstrapping is the one operation that does not require an existing
    membership — the creator's owner membership is created in the same
    transaction, so no organization can exist without an owner.
    """

    def run(
        self,
        principal: Principal,
        *,
        name: str,
        slug: str,
        title: str | None = None,
    ) -> OrganizationView:
        with self._uow_factory() as uow:
            parsed_slug = Slug(slug)
            if uow.organizations.slug_exists(parsed_slug):
                raise InvalidInput("organization slug is already taken")
            organization = Organization.create(slug=parsed_slug, name=name)
            uow.organizations.add(organization)
            membership = OrganizationMembership.grant(
                organization_id=organization.id,
                user_id=str(principal.user_id),
                role=OrganizationRole.ORG_OWNER,
                title=title,
            )
            uow.organization_memberships.add(membership)
            uow.commit()
            return OrganizationView(
                organization=organization,
                membership=membership,
                capabilities=frozenset(OrgPermission),
            )


class ListMyOrganizations(OrganizationUseCase):
    """List the organizations the caller is an active member of."""

    def run(self, principal: Principal) -> list[OrganizationView]:
        with self._uow_factory() as uow:
            organizations = uow.organizations.list_for_user(principal.user_id)
            views: list[OrganizationView] = []
            for organization in organizations:
                membership = uow.organization_memberships.get_for_user(
                    organization.id, principal.user_id
                )
                if membership is None:
                    continue
                views.append(
                    OrganizationView(
                        organization=organization,
                        membership=membership,
                        capabilities=capabilities_for(membership.role),
                    )
                )
            return views


class GetOrganization(OrganizationUseCase):
    """Fetch one organization the caller belongs to."""

    def run(self, principal: Principal, *, organization_id: OrganizationId) -> OrganizationView:
        with self._uow_factory() as uow:
            organization, membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            member = self._require_permission(
                organization=organization,
                membership=membership,
                permission=OrgPermission.VIEW_ORGANIZATION,
            )
            return OrganizationView(
                organization=organization,
                membership=member,
                capabilities=capabilities_for(member.role),
            )


class UpdateOrganization(OrganizationUseCase):
    """Rename an organization (and optionally change its status)."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        name: str | None = None,
        status: str | None = None,
        expected_version: int | None = None,
    ) -> OrganizationView:
        with self._uow_factory() as uow:
            organization, membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            member = self._require_permission(
                organization=organization,
                membership=membership,
                permission=OrgPermission.MANAGE_ORGANIZATION,
            )
            if name is not None:
                organization.rename(name=name)
            if status is not None:
                organization.change_status(status=OrganizationStatus(status))
            uow.organizations.save(organization, expected_version=expected_version)
            uow.commit()
            return OrganizationView(
                organization=organization,
                membership=member,
                capabilities=capabilities_for(member.role),
            )


# -- units -----------------------------------------------------------------


class CreateOrganizationUnit(OrganizationUseCase):
    """Create a unit (division/department/branch/program) in an organization."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        name: str,
        slug: str,
        unit_type: OrgUnitType = OrgUnitType.DEPARTMENT,
        parent_id: OrganizationUnitId | None = None,
    ) -> OrganizationUnit:
        with self._uow_factory() as uow:
            organization, membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=membership,
                permission=OrgPermission.MANAGE_UNITS,
            )
            if parent_id is not None:
                parent = uow.organization_units.get(parent_id)
                # A parent from another organization must not be attachable.
                if parent is None or str(parent.organization_id) != str(organization.id):
                    raise InvalidInput("parent unit does not exist in this organization")
                parent_path = uow.organization_units.ancestor_path(parent_id)
                parent_path.append(parent_id)
            else:
                parent_path = []
            unit = OrganizationUnit.create(
                organization_id=organization.id,
                name=name,
                slug=slug,
                unit_type=unit_type,
                parent_id=parent_id,
            )
            unit.assert_can_attach_to(
                parent_id=parent_id, parent_path=parent_path[:-1] if parent_id else []
            )
            if uow.organization_units.slug_exists(organization.id, unit.slug):
                raise InvalidInput("a unit with this slug already exists in the organization")
            uow.organization_units.add(unit)
            uow.commit()
            return unit


class ListOrganizationUnits(OrganizationUseCase):
    """List the units of an organization the caller belongs to."""

    def run(
        self, principal: Principal, *, organization_id: OrganizationId
    ) -> list[OrganizationUnit]:
        with self._uow_factory() as uow:
            organization, membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=membership,
                permission=OrgPermission.VIEW_ORGANIZATION,
            )
            return uow.organization_units.list_for_organization(organization.id)


class MoveOrganizationUnit(OrganizationUseCase):
    """Re-parent a unit, rejecting cycles and depth violations."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        unit_id: OrganizationUnitId,
        parent_id: OrganizationUnitId | None,
    ) -> OrganizationUnit:
        with self._uow_factory() as uow:
            organization, membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=membership,
                permission=OrgPermission.MANAGE_UNITS,
            )
            unit = uow.organization_units.get(unit_id)
            if unit is None or str(unit.organization_id) != str(organization.id):
                raise ResourceNotFound("unit not found")
            parent_path: list[OrganizationUnitId] = []
            if parent_id is not None:
                parent = uow.organization_units.get(parent_id)
                if parent is None or str(parent.organization_id) != str(organization.id):
                    raise InvalidInput("parent unit does not exist in this organization")
                parent_path = uow.organization_units.ancestor_path(parent_id)
                parent_path.append(parent_id)
            unit.move_to(parent_id=parent_id, parent_path=parent_path)
            uow.organization_units.save(unit)
            uow.commit()
            return unit


# -- teams -----------------------------------------------------------------


class CreateTeam(OrganizationUseCase):
    """Create a team inside a unit of an organization."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        unit_id: OrganizationUnitId,
        name: str,
        slug: str,
        parent_team_id: TeamId | None = None,
    ) -> Team:
        with self._uow_factory() as uow:
            organization, membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=membership,
                permission=OrgPermission.MANAGE_TEAMS,
            )
            unit = uow.organization_units.get(unit_id)
            if unit is None or str(unit.organization_id) != str(organization.id):
                raise InvalidInput("unit does not exist in this organization")
            team = Team.create(
                organization_id=organization.id,
                unit_id=unit.id,
                name=name,
                slug=slug,
                parent_team_id=parent_team_id,
            )
            if uow.teams.slug_exists(unit.id, team.slug):
                raise InvalidInput("a team with this slug already exists in the unit")
            if parent_team_id is not None:
                parent_team = uow.teams.get(parent_team_id)
                if parent_team is None or str(parent_team.organization_id) != str(organization.id):
                    raise InvalidInput("parent team does not exist in this organization")
                path = uow.teams.ancestor_path(parent_team_id)
                path.append(parent_team_id)
                team.set_parent(parent_team_id=parent_team_id, parent_path=path[:-1])
            uow.teams.add(team)
            uow.commit()
            return team


class ListTeams(OrganizationUseCase):
    """List teams in an organization, optionally restricted to one unit."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        unit_id: OrganizationUnitId | None = None,
    ) -> list[Team]:
        with self._uow_factory() as uow:
            organization, membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=membership,
                permission=OrgPermission.VIEW_ORGANIZATION,
            )
            if unit_id is not None:
                return uow.teams.list_for_unit(unit_id)
            return uow.teams.list_for_organization(organization.id)


# -- memberships -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemberView:
    """A member with their organization membership and team memberships."""

    membership: OrganizationMembership
    team_memberships: list[TeamMembership]


class InviteOrganizationMember(OrganizationUseCase):
    """Add a person to the organization with an org role."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        user_id: UserId,
        role: OrganizationRole = OrganizationRole.ORG_MEMBER,
        manager_user_id: UserId | None = None,
        title: str | None = None,
    ) -> OrganizationMembership:
        with self._uow_factory() as uow:
            organization, membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            actor = self._require_permission(
                organization=organization,
                membership=membership,
                permission=OrgPermission.MANAGE_MEMBERSHIPS,
            )
            self._assert_may_assign_role(actor=actor, role=role)
            if uow.users.get(user_id) is None:
                raise InvalidInput("user does not exist")
            if uow.organization_memberships.get_for_user(organization.id, user_id) is not None:
                raise InvalidInput("user is already a member of this organization")
            if manager_user_id is not None and (
                uow.organization_memberships.get_for_user(organization.id, manager_user_id) is None
            ):
                raise InvalidInput("manager is not a member of this organization")
            new_membership = OrganizationMembership.grant(
                organization_id=organization.id,
                user_id=str(user_id),
                role=role,
                manager_user_id=str(manager_user_id) if manager_user_id else None,
                title=title,
            )
            uow.organization_memberships.add(new_membership)
            uow.commit()
            return new_membership

    @staticmethod
    def _assert_may_assign_role(*, actor: OrganizationMembership, role: OrganizationRole) -> None:
        """Prevent privilege escalation: you may not grant a role above your own."""
        if not actor.role.at_least(role):
            raise AccessDenied("cannot assign a role more privileged than your own")


class ListOrganizationMembers(OrganizationUseCase):
    """List the members of an organization."""

    def run(
        self, principal: Principal, *, organization_id: OrganizationId
    ) -> list[OrganizationMembership]:
        with self._uow_factory() as uow:
            organization, membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=membership,
                permission=OrgPermission.VIEW_ORGANIZATION,
            )
            return uow.organization_memberships.list_for_organization(organization.id)


class ChangeMemberRole(OrganizationUseCase):
    """Change a member's organization role, guarding the last administrator."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        membership_id: OrganizationMembershipId,
        role: OrganizationRole,
    ) -> OrganizationMembership:
        with self._uow_factory() as uow:
            organization, actor_membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            actor = self._require_permission(
                organization=organization,
                membership=actor_membership,
                permission=OrgPermission.MANAGE_ROLES,
            )
            target = uow.organization_memberships.get(membership_id)
            if target is None or str(target.organization_id) != str(organization.id):
                raise ResourceNotFound("membership not found")
            InviteOrganizationMember._assert_may_assign_role(actor=actor, role=role)
            # Demoting the final active admin would strand the organization.
            if not role.is_administrative:
                self._assert_not_last_admin(uow, organization.id, target)
            target.change_role(role=role)
            uow.organization_memberships.save(target)
            uow.commit()
            return target


class SetMemberManager(OrganizationUseCase):
    """Set or clear a member's reporting line inside the organization.

    The manager must be an active member of the *same* organization, and the new
    relationship must not create a reporting cycle. Both are checked here rather
    than in the domain because they require the other memberships.
    """

    MAX_REPORTING_DEPTH = 32

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        membership_id: OrganizationMembershipId,
        manager_user_id: UserId | None,
    ) -> OrganizationMembership:
        with self._uow_factory() as uow:
            organization, actor_membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=actor_membership,
                permission=OrgPermission.MANAGE_MEMBERSHIPS,
            )
            target = uow.organization_memberships.get(membership_id)
            if target is None or str(target.organization_id) != str(organization.id):
                raise ResourceNotFound("membership not found")
            if manager_user_id is not None:
                manager = uow.organization_memberships.get_for_user(
                    organization.id, manager_user_id
                )
                if manager is None or not manager.grants_access:
                    raise InvalidInput("manager is not an active member of this organization")
                self._assert_no_reporting_cycle(
                    uow=uow,
                    organization_id=organization.id,
                    member_user_id=target.user_id,
                    manager_user_id=str(manager_user_id),
                )
            target.assign_manager(manager_user_id=str(manager_user_id) if manager_user_id else None)
            uow.organization_memberships.save(target)
            uow.commit()
            return target

    def _assert_no_reporting_cycle(
        self,
        *,
        uow: UnitOfWork,
        organization_id: OrganizationId,
        member_user_id: str,
        manager_user_id: str,
    ) -> None:
        """Walk the manager chain; reject if it reaches the member again."""
        seen = {member_user_id}
        current: str | None = manager_user_id
        hops = 0
        while current is not None:
            if current in seen:
                raise InvalidInput("this reporting line would create a cycle")
            seen.add(current)
            hops += 1
            if hops > self.MAX_REPORTING_DEPTH:
                raise InvalidInput("reporting line is too deep")
            membership = uow.organization_memberships.get_for_user(organization_id, UserId(current))
            current = membership.manager_user_id if membership else None


class SuspendMember(OrganizationUseCase):
    """Suspend a member: a reversible pause that removes all authority."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        membership_id: OrganizationMembershipId,
    ) -> OrganizationMembership:
        with self._uow_factory() as uow:
            organization, actor_membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=actor_membership,
                permission=OrgPermission.MANAGE_MEMBERSHIPS,
            )
            target = uow.organization_memberships.get(membership_id)
            if target is None or str(target.organization_id) != str(organization.id):
                raise ResourceNotFound("membership not found")
            self._assert_not_last_admin(uow, organization.id, target)
            target.suspend()
            uow.organization_memberships.save(target)
            uow.commit()
            return target


class ReactivateMember(OrganizationUseCase):
    """Lift a suspension, restoring the member's authority.

    Suspension is documented as a reversible pause, so it needs an inverse.
    Deprovisioned memberships stay terminal: offboarding is not undone here.
    """

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        membership_id: OrganizationMembershipId,
    ) -> OrganizationMembership:
        with self._uow_factory() as uow:
            organization, actor_membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=actor_membership,
                permission=OrgPermission.MANAGE_MEMBERSHIPS,
            )
            target = uow.organization_memberships.get(membership_id)
            if target is None or str(target.organization_id) != str(organization.id):
                raise ResourceNotFound("membership not found")
            if target.status is MembershipStatus.DEPROVISIONED:
                # Offboarding is terminal, so this is a rejected state change, not
                # a permission failure: the caller may well be allowed to manage
                # memberships. Report it as invalid input rather than a 403.
                raise InvalidInput("a deprovisioned membership cannot be reactivated")
            if not target.grants_access:
                target.reactivate()
                uow.organization_memberships.save(target)
                uow.commit()
            return target


class DeprovisionMember(OrganizationUseCase):
    """Deprovision a member: terminal offboarding, clearing the reporting line."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        membership_id: OrganizationMembershipId,
    ) -> OrganizationMembership:
        with self._uow_factory() as uow:
            organization, actor_membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=actor_membership,
                permission=OrgPermission.MANAGE_MEMBERSHIPS,
            )
            target = uow.organization_memberships.get(membership_id)
            if target is None or str(target.organization_id) != str(organization.id):
                raise ResourceNotFound("membership not found")
            self._assert_not_last_admin(uow, organization.id, target)
            target.deprovision()
            uow.organization_memberships.save(target)
            uow.commit()
            return target


# -- team memberships ------------------------------------------------------


class AddTeamMember(OrganizationUseCase):
    """Add an existing organization member to a team."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        team_id: TeamId,
        user_id: UserId,
        role: TeamRole = TeamRole.TEAM_MEMBER,
    ) -> TeamMembership:
        with self._uow_factory() as uow:
            organization, actor_membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=actor_membership,
                permission=OrgPermission.MANAGE_TEAMS,
            )
            team = uow.teams.get(team_id)
            if team is None or str(team.organization_id) != str(organization.id):
                raise ResourceNotFound("team not found")
            # Team membership never substitutes for organization membership: a
            # non-member must not be able to acquire team access by invitation.
            org_membership = uow.organization_memberships.get_for_user(organization.id, user_id)
            if org_membership is None or not org_membership.grants_access:
                raise InvalidInput("user is not an active member of this organization")
            existing = uow.team_memberships.get_for_user(team.id, user_id)
            if existing is not None:
                # Removal is a soft revoke that keeps the row for audit history,
                # so rejoining a team must revive it rather than be refused: a
                # team is organisation-only, never a tenant boundary.
                if existing.grants_access:
                    raise InvalidInput("user is already a member of this team")
                existing.reactivate(role=role)
                uow.team_memberships.save(existing)
                uow.commit()
                return existing
            membership = TeamMembership.grant(team_id=team.id, user_id=str(user_id), role=role)
            uow.team_memberships.add(membership)
            uow.commit()
            return membership


class RemoveTeamMember(OrganizationUseCase):
    """Remove a person from a team."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        team_id: TeamId,
        user_id: UserId,
    ) -> None:
        with self._uow_factory() as uow:
            organization, actor_membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=actor_membership,
                permission=OrgPermission.MANAGE_TEAMS,
            )
            team = uow.teams.get(team_id)
            if team is None or str(team.organization_id) != str(organization.id):
                raise ResourceNotFound("team not found")
            membership = uow.team_memberships.get_for_user(team.id, user_id)
            if membership is None:
                raise ResourceNotFound("team membership not found")
            membership.remove()
            uow.team_memberships.save(membership)
            uow.commit()


class ListTeamMembers(OrganizationUseCase):
    """List the members of one team."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        team_id: TeamId,
    ) -> list[TeamMembership]:
        with self._uow_factory() as uow:
            organization, membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=membership,
                permission=OrgPermission.VIEW_ORGANIZATION,
            )
            team = uow.teams.get(team_id)
            if team is None or str(team.organization_id) != str(organization.id):
                raise ResourceNotFound("team not found")
            return uow.team_memberships.list_for_team(team.id)


__all__ = [
    "AddTeamMember",
    "ChangeMemberRole",
    "CreateOrganization",
    "CreateOrganizationUnit",
    "CreateTeam",
    "DeprovisionMember",
    "GetOrganization",
    "InviteOrganizationMember",
    "ListMyOrganizations",
    "ListOrganizationMembers",
    "ListOrganizationUnits",
    "ListTeamMembers",
    "ListTeams",
    "MemberView",
    "MoveOrganizationUnit",
    "OrganizationUseCase",
    "OrganizationView",
    "RemoveTeamMember",
    "SetMemberManager",
    "SuspendMember",
    "UpdateOrganization",
]
