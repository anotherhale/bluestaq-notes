"""Tenant-access rule tests.

The rule decides whether an authenticated person may still act. It is the
security property behind "revoking a membership takes effect on the next
request", so these tests pin each branch, including the bootstrap case that
must stay permissive so a new identity can create its first organization.
"""

from __future__ import annotations

import pytest

from notes_service.domain.organization.entities import Organization, OrganizationMembership
from notes_service.domain.organization.tenant_access import (
    TenantAccessStatus,
    evaluate_tenant_access,
)
from notes_service.domain.organization.value_objects import (
    MembershipStatus,
    OrganizationStatus,
)

USER = "11111111-1111-1111-1111-111111111111"


def _org(slug: str, status: OrganizationStatus = OrganizationStatus.ACTIVE) -> Organization:
    return Organization.create(slug=slug, name=slug.title(), status=status)


def _membership(
    organization: Organization, status: MembershipStatus = MembershipStatus.ACTIVE
) -> OrganizationMembership:
    return OrganizationMembership.grant(
        organization_id=organization.id, user_id=USER, status=status
    )


def test_no_membership_is_unaffiliated_and_allowed_to_bootstrap() -> None:
    access = evaluate_tenant_access(memberships=[], organizations={})
    assert access.status is TenantAccessStatus.UNAFFILIATED
    # Allowed so a brand-new identity can create its first organization. It is
    # not a grant inside any existing tenant.
    assert access.allowed is True


def test_active_membership_in_operational_org_grants_access() -> None:
    org = _org("acme")
    access = evaluate_tenant_access(memberships=[_membership(org)], organizations={org.id: org})
    assert access.status is TenantAccessStatus.GRANTED
    assert access.allowed is True
    assert access.organization_ids == frozenset({org.id})


@pytest.mark.parametrize("status", [MembershipStatus.SUSPENDED, MembershipStatus.DEPROVISIONED])
def test_inactive_membership_denies_access(status: MembershipStatus) -> None:
    org = _org("acme")
    access = evaluate_tenant_access(
        memberships=[_membership(org, status=status)], organizations={org.id: org}
    )
    assert access.status is TenantAccessStatus.DENIED
    assert access.allowed is False


@pytest.mark.parametrize("status", [OrganizationStatus.SUSPENDED, OrganizationStatus.ARCHIVED])
def test_non_operational_org_denies_access(status: OrganizationStatus) -> None:
    """Suspending a tenant revokes access for everyone in it at once."""
    org = _org("acme", status=status)
    access = evaluate_tenant_access(memberships=[_membership(org)], organizations={org.id: org})
    assert access.status is TenantAccessStatus.DENIED


def test_one_active_membership_among_many_grants_access() -> None:
    """Access is revoked only when *no* tenant still grants it."""
    revoked = _org("old-co")
    live = _org("new-co")
    access = evaluate_tenant_access(
        memberships=[
            _membership(revoked, status=MembershipStatus.DEPROVISIONED),
            _membership(live),
        ],
        organizations={revoked.id: revoked, live.id: live},
    )
    assert access.status is TenantAccessStatus.GRANTED
    assert access.organization_ids == frozenset({live.id})


def test_membership_whose_org_cannot_be_loaded_fails_closed() -> None:
    """A dangling membership reference must not grant access."""
    org = _org("acme")
    access = evaluate_tenant_access(memberships=[_membership(org)], organizations={})
    assert access.status is TenantAccessStatus.DENIED
