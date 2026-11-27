"""The organization domain package.

Public surface for the organization bounded context. Importing from here keeps
call sites stable if the internal module layout changes.
"""

from __future__ import annotations

from notes_service.domain.organization.entities import (
    Organization,
    OrganizationMembership,
    OrganizationUnit,
    Team,
    TeamMembership,
)
from notes_service.domain.organization.value_objects import (
    MAX_HIERARCHY_DEPTH,
    MAX_NAME_LENGTH,
    MAX_SLUG_LENGTH,
    ROLE_CAPABILITIES,
    MembershipStatus,
    OrganizationId,
    OrganizationMembershipId,
    OrganizationName,
    OrganizationRole,
    OrganizationStatus,
    OrganizationUnitId,
    OrgPermission,
    OrgUnitType,
    Slug,
    TeamId,
    TeamMembershipId,
    TeamRole,
    TeamStatus,
    UnitStatus,
    capabilities_for,
)

__all__ = [
    "MAX_HIERARCHY_DEPTH",
    "MAX_NAME_LENGTH",
    "MAX_SLUG_LENGTH",
    "ROLE_CAPABILITIES",
    "MembershipStatus",
    "OrgPermission",
    "OrgUnitType",
    "Organization",
    "OrganizationId",
    "OrganizationMembership",
    "OrganizationMembershipId",
    "OrganizationName",
    "OrganizationRole",
    "OrganizationStatus",
    "OrganizationUnit",
    "OrganizationUnitId",
    "Slug",
    "Team",
    "TeamId",
    "TeamMembership",
    "TeamMembershipId",
    "TeamRole",
    "TeamStatus",
    "UnitStatus",
    "capabilities_for",
]
