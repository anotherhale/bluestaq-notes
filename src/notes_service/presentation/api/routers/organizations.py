"""Organization administration endpoints under ``/api/v1/organizations``.

This is the enterprise tenant-management surface: the organization hierarchy
(units, teams, memberships) and the admin actions that maintain it. Every
endpoint delegates its authorization decision to the application layer, which
resolves the caller's membership and checks the required ``OrgPermission``.
The router therefore never decides who may do what; it only maps HTTP to use
cases.

Tenant isolation is structural: an organization the caller does not belong to
is reported as 404 (not 403), so organization ids cannot be probed. A caller
who *is* a member but lacks the capability gets 403 from the application layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter, Response, status

from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.organization.value_objects import (
    OrganizationId,
    OrganizationMembershipId,
    OrganizationUnitId,
    TeamId,
)
from notes_service.presentation.dependencies import ContainerDep, PrincipalDep, RateLimitDep
from notes_service.presentation.schemas import (
    MemberInviteRequest,
    MemberManagerRequest,
    MemberRoleChangeRequest,
    MembershipListResponse,
    MembershipResponse,
    OrganizationCreateRequest,
    OrganizationListResponse,
    OrganizationResponse,
    OrganizationUnitCreateRequest,
    OrganizationUnitListResponse,
    OrganizationUnitMoveRequest,
    OrganizationUnitResponse,
    OrganizationUpdateRequest,
    PageMeta,
    TeamCreateRequest,
    TeamListResponse,
    TeamMemberAddRequest,
    TeamMembershipListResponse,
    TeamMembershipResponse,
    TeamResponse,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])


def _page(items: Sequence[object]) -> PageMeta:
    """These listings are unpaginated, so the meta describes the full set."""
    return PageMeta(total=len(items), limit=len(items), offset=0)


# -- organizations ---------------------------------------------------------


@router.get(
    "",
    response_model=OrganizationListResponse,
    summary="List the organizations the caller belongs to",
    dependencies=[RateLimitDep],
)
def list_my_organizations(
    principal: PrincipalDep, container: ContainerDep
) -> OrganizationListResponse:
    views = container.list_my_organizations().run(principal)
    items = [OrganizationResponse.from_view(view) for view in views]
    return OrganizationListResponse(items=items, meta=_page(items))


@router.post(
    "",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organization owned by the caller",
    dependencies=[RateLimitDep],
)
def create_organization(
    payload: OrganizationCreateRequest, principal: PrincipalDep, container: ContainerDep
) -> OrganizationResponse:
    view = container.create_organization().run(
        principal, name=payload.name, slug=payload.slug, title=payload.title
    )
    return OrganizationResponse.from_view(view)


@router.get(
    "/{organization_id}",
    response_model=OrganizationResponse,
    summary="Get one organization the caller belongs to",
    dependencies=[RateLimitDep],
)
def get_organization(
    organization_id: UUID, principal: PrincipalDep, container: ContainerDep
) -> OrganizationResponse:
    view = container.get_organization().run(
        principal, organization_id=OrganizationId(str(organization_id))
    )
    return OrganizationResponse.from_view(view)


@router.patch(
    "/{organization_id}",
    response_model=OrganizationResponse,
    summary="Rename or change the status of an organization",
    dependencies=[RateLimitDep],
)
def update_organization(
    organization_id: UUID,
    payload: OrganizationUpdateRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> OrganizationResponse:
    view = container.update_organization().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        name=payload.name,
        status=payload.status.value if payload.status else None,
        expected_version=payload.version,
    )
    return OrganizationResponse.from_view(view)


# -- organization units ----------------------------------------------------


@router.get(
    "/{organization_id}/units",
    response_model=OrganizationUnitListResponse,
    summary="List the units of an organization",
    dependencies=[RateLimitDep],
)
def list_organization_units(
    organization_id: UUID, principal: PrincipalDep, container: ContainerDep
) -> OrganizationUnitListResponse:
    units = container.list_organization_units().run(
        principal, organization_id=OrganizationId(str(organization_id))
    )
    items = [OrganizationUnitResponse.from_domain(unit) for unit in units]
    return OrganizationUnitListResponse(items=items, meta=_page(items))


@router.post(
    "/{organization_id}/units",
    response_model=OrganizationUnitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a unit (division, department, ...) in an organization",
    dependencies=[RateLimitDep],
)
def create_organization_unit(
    organization_id: UUID,
    payload: OrganizationUnitCreateRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> OrganizationUnitResponse:
    unit = container.create_organization_unit().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        name=payload.name,
        slug=payload.slug,
        unit_type=payload.unit_type,
        parent_id=OrganizationUnitId(str(payload.parent_id)) if payload.parent_id else None,
    )
    return OrganizationUnitResponse.from_domain(unit)


@router.patch(
    "/{organization_id}/units/{unit_id}/parent",
    response_model=OrganizationUnitResponse,
    summary="Re-parent a unit (null promotes it to the organization root)",
    dependencies=[RateLimitDep],
)
def move_organization_unit(
    organization_id: UUID,
    unit_id: UUID,
    payload: OrganizationUnitMoveRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> OrganizationUnitResponse:
    unit = container.move_organization_unit().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        unit_id=OrganizationUnitId(str(unit_id)),
        parent_id=OrganizationUnitId(str(payload.parent_id)) if payload.parent_id else None,
    )
    return OrganizationUnitResponse.from_domain(unit)


# -- teams -----------------------------------------------------------------


@router.get(
    "/{organization_id}/teams",
    response_model=TeamListResponse,
    summary="List the teams of an organization",
    dependencies=[RateLimitDep],
)
def list_teams(
    organization_id: UUID, principal: PrincipalDep, container: ContainerDep
) -> TeamListResponse:
    teams = container.list_teams().run(
        principal, organization_id=OrganizationId(str(organization_id))
    )
    items = [TeamResponse.from_domain(team) for team in teams]
    return TeamListResponse(items=items, meta=_page(items))


@router.post(
    "/{organization_id}/teams",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a team inside an organization unit",
    dependencies=[RateLimitDep],
)
def create_team(
    organization_id: UUID,
    payload: TeamCreateRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> TeamResponse:
    team = container.create_team().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        unit_id=OrganizationUnitId(str(payload.unit_id)),
        name=payload.name,
        slug=payload.slug,
        parent_team_id=TeamId(str(payload.parent_team_id)) if payload.parent_team_id else None,
    )
    return TeamResponse.from_domain(team)


# -- memberships -----------------------------------------------------------


@router.get(
    "/{organization_id}/members",
    response_model=MembershipListResponse,
    summary="List the members of an organization",
    dependencies=[RateLimitDep],
)
def list_organization_members(
    organization_id: UUID, principal: PrincipalDep, container: ContainerDep
) -> MembershipListResponse:
    members = container.list_organization_members().run(
        principal, organization_id=OrganizationId(str(organization_id))
    )
    items = [MembershipResponse.from_domain(member) for member in members]
    return MembershipListResponse(items=items, meta=_page(items))


@router.post(
    "/{organization_id}/members",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite an existing user into the organization",
    dependencies=[RateLimitDep],
)
def invite_organization_member(
    organization_id: UUID,
    payload: MemberInviteRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> MembershipResponse:
    membership = container.invite_organization_member().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        user_id=UserId(str(payload.user_id)),
        role=payload.role,
        manager_user_id=UserId(str(payload.manager_user_id)) if payload.manager_user_id else None,
        title=payload.title,
    )
    return MembershipResponse.from_domain(membership)


@router.patch(
    "/{organization_id}/members/{membership_id}/role",
    response_model=MembershipResponse,
    summary="Change a member's organization role",
    dependencies=[RateLimitDep],
)
def change_member_role(
    organization_id: UUID,
    membership_id: UUID,
    payload: MemberRoleChangeRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> MembershipResponse:
    membership = container.change_member_role().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        membership_id=OrganizationMembershipId(str(membership_id)),
        role=payload.role,
    )
    return MembershipResponse.from_domain(membership)


@router.patch(
    "/{organization_id}/members/{membership_id}/manager",
    response_model=MembershipResponse,
    summary="Set or clear a member's reporting line",
    dependencies=[RateLimitDep],
)
def set_member_manager(
    organization_id: UUID,
    membership_id: UUID,
    payload: MemberManagerRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> MembershipResponse:
    membership = container.set_member_manager().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        membership_id=OrganizationMembershipId(str(membership_id)),
        manager_user_id=UserId(str(payload.manager_user_id)) if payload.manager_user_id else None,
    )
    return MembershipResponse.from_domain(membership)


@router.post(
    "/{organization_id}/members/{membership_id}/suspend",
    response_model=MembershipResponse,
    summary="Suspend a member (reversible: removes all authority)",
    dependencies=[RateLimitDep],
)
def suspend_member(
    organization_id: UUID,
    membership_id: UUID,
    principal: PrincipalDep,
    container: ContainerDep,
) -> MembershipResponse:
    membership = container.suspend_member().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        membership_id=OrganizationMembershipId(str(membership_id)),
    )
    return MembershipResponse.from_domain(membership)


@router.post(
    "/{organization_id}/members/{membership_id}/reactivate",
    response_model=MembershipResponse,
    summary="Reactivate a suspended member (inverse of suspend)",
    dependencies=[RateLimitDep],
)
def reactivate_member(
    organization_id: UUID,
    membership_id: UUID,
    principal: PrincipalDep,
    container: ContainerDep,
) -> MembershipResponse:
    membership = container.reactivate_member().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        membership_id=OrganizationMembershipId(str(membership_id)),
    )
    return MembershipResponse.from_domain(membership)


@router.post(
    "/{organization_id}/members/{membership_id}/deprovision",
    response_model=MembershipResponse,
    summary="Deprovision a member (terminal offboarding)",
    dependencies=[RateLimitDep],
)
def deprovision_member(
    organization_id: UUID,
    membership_id: UUID,
    principal: PrincipalDep,
    container: ContainerDep,
) -> MembershipResponse:
    membership = container.deprovision_member().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        membership_id=OrganizationMembershipId(str(membership_id)),
    )
    return MembershipResponse.from_domain(membership)


# -- team memberships ------------------------------------------------------


@router.get(
    "/{organization_id}/teams/{team_id}/members",
    response_model=TeamMembershipListResponse,
    summary="List the members of one team",
    dependencies=[RateLimitDep],
)
def list_team_members(
    organization_id: UUID,
    team_id: UUID,
    principal: PrincipalDep,
    container: ContainerDep,
) -> TeamMembershipListResponse:
    members = container.list_team_members().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        team_id=TeamId(str(team_id)),
    )
    items = [TeamMembershipResponse.from_domain(member) for member in members]
    return TeamMembershipListResponse(items=items, meta=_page(items))


@router.post(
    "/{organization_id}/teams/{team_id}/members",
    response_model=TeamMembershipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an existing organization member to a team",
    dependencies=[RateLimitDep],
)
def add_team_member(
    organization_id: UUID,
    team_id: UUID,
    payload: TeamMemberAddRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> TeamMembershipResponse:
    membership = container.add_team_member().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        team_id=TeamId(str(team_id)),
        user_id=UserId(str(payload.user_id)),
        role=payload.role,
    )
    return TeamMembershipResponse.from_domain(membership)


@router.delete(
    "/{organization_id}/teams/{team_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a person from a team",
    dependencies=[RateLimitDep],
)
def remove_team_member(
    organization_id: UUID,
    team_id: UUID,
    user_id: UUID,
    principal: PrincipalDep,
    container: ContainerDep,
) -> Response:
    container.remove_team_member().run(
        principal,
        organization_id=OrganizationId(str(organization_id)),
        team_id=TeamId(str(team_id)),
        user_id=UserId(str(user_id)),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
