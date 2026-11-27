"""Whether an authenticated user may still act within a tenant.

Authentication answers *who* the caller is. It does not, on its own, establish
that the caller still belongs to any tenant. Without a separate check, a
suspended or deprovisioned membership would keep working until the session
happened to expire, which is exactly the offboarding gap this rule closes.

The rule is stated over the caller's *memberships* rather than a single
organization because a person may belong to several tenants: access is revoked
only when none of them still grants it. An active membership is necessary but
not sufficient — the organization itself must also be operational, so
suspending a tenant revokes access for everyone in it at once.

Deliberately no caching: the point of the rule is that a status change takes
effect on the next request.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from notes_service.domain.organization.entities import Organization, OrganizationMembership
from notes_service.domain.organization.value_objects import OrganizationId


class TenantAccessStatus(StrEnum):
    """Outcome of evaluating a user's tenant access."""

    #: At least one active membership in an operational organization.
    GRANTED = "granted"
    #: The user has memberships, but every one is suspended, deprovisioned, or
    #: points at a non-operational organization. Access is refused.
    DENIED = "denied"
    #: The user has no membership rows at all. This is the pre-provisioning
    #: state of a brand-new identity, which must be able to create its first
    #: organization (see ``CreateOrganization``, which needs no membership).
    UNAFFILIATED = "unaffiliated"


@dataclass(frozen=True, slots=True)
class TenantAccess:
    """The evaluated result, carrying enough detail to explain a refusal."""

    status: TenantAccessStatus
    organization_ids: frozenset[OrganizationId] = frozenset()

    @property
    def allowed(self) -> bool:
        """True when the caller may proceed past the identity boundary.

        ``UNAFFILIATED`` is allowed so that a new identity can bootstrap its
        first organization; it is *not* an authorization grant for any existing
        tenant, and every tenant-scoped use case still requires a membership.
        """
        return self.status is not TenantAccessStatus.DENIED


def evaluate_tenant_access(
    *,
    memberships: list[OrganizationMembership],
    organizations: dict[OrganizationId, Organization],
) -> TenantAccess:
    """Decide tenant access from the caller's memberships.

    ``organizations`` maps organization id to the loaded organization for every
    organization referenced by ``memberships``. It is passed in rather than
    fetched here so this rule stays pure and testable.
    """
    if not memberships:
        return TenantAccess(status=TenantAccessStatus.UNAFFILIATED)

    granted: set[OrganizationId] = set()
    for membership in memberships:
        if not membership.grants_access:
            continue
        organization = organizations.get(membership.organization_id)
        if organization is None or not organization.is_operational:
            continue
        granted.add(membership.organization_id)

    if not granted:
        return TenantAccess(status=TenantAccessStatus.DENIED)
    return TenantAccess(status=TenantAccessStatus.GRANTED, organization_ids=frozenset(granted))


__all__ = ["TenantAccess", "TenantAccessStatus", "evaluate_tenant_access"]
