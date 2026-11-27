"""Default organization authorization policy.

Deny-by-default over the role/capability matrix in the domain, with two
additional deterministic guards that a pure role matrix cannot express:

1. **A suspended or archived organization grants nothing**, regardless of role.
   Offboarding a tenant must take effect immediately, not only after every
   membership is individually touched.
2. **An inactive membership grants nothing.** ``SUSPENDED`` is a pause and
   ``DEPROVISIONED`` is terminal; neither may retain administrative authority.
"""

from __future__ import annotations

from notes_service.application.ports.authorization import Decision
from notes_service.domain.organization.entities import Organization, OrganizationMembership
from notes_service.domain.organization.value_objects import OrgPermission, capabilities_for


class DefaultOrganizationAuthorizationProvider:
    """Role-based administration policy with deny-by-default semantics."""

    @property
    def name(self) -> str:
        return "default_organization"

    def decide(
        self,
        *,
        organization: Organization,
        membership: OrganizationMembership | None,
        permission: OrgPermission,
    ) -> Decision:
        if not organization.is_operational:
            return Decision.deny("organization_not_operational")
        if membership is None:
            return Decision.deny("not_a_member")
        if not membership.grants_access:
            return Decision.deny(f"membership_{membership.status.value}")
        if str(membership.organization_id) != str(organization.id):
            # A membership from another tenant must never be accepted, even if a
            # caller mistakenly passes it. This is the last line of defence
            # against a cross-tenant read.
            return Decision.deny("membership_wrong_organization")
        if permission in capabilities_for(membership.role):
            return Decision.allow(f"role_{membership.role.value}")
        return Decision.deny("insufficient_role")


__all__ = ["DefaultOrganizationAuthorizationProvider"]
