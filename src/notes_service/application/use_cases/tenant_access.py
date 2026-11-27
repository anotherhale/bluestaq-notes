"""Enforce that an authenticated user still has tenant access.

This is the application-layer seam between "the caller proved who they are" and
"the caller may still act". It is shared by every path that establishes or
restores an identity: the OIDC login callback, per-request session resolution,
the bearer-token provider, and agent-token resolution (where the *delegator*
must still be entitled, because an agent acts as its delegator). Keeping it in
one place is what stops the paths from drifting apart — a membership revocation
must take effect everywhere or nowhere.

The decision itself is the pure domain rule in
``domain/organization/tenant_access.py``; this module only loads the data it
needs and turns a refusal into the transport-neutral
:class:`AuthenticationError` the presentation layer already maps to ``401``.
"""

from __future__ import annotations

from notes_service.application.errors import AuthenticationError
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.organization.entities import Organization
from notes_service.domain.organization.tenant_access import (
    TenantAccess,
    TenantAccessStatus,
    evaluate_tenant_access,
)
from notes_service.domain.organization.value_objects import OrganizationId

#: Deliberately uniform: a caller must not learn whether an account exists, is
#: unaffiliated, or was deprovisioned. The distinction stays server-side.
_DENIED_MESSAGE = "account is not entitled to access this tenant"


def resolve_tenant_access(uow: UnitOfWork, *, user_id: str | UserId) -> TenantAccess:
    """Load the caller's memberships and evaluate tenant access.

    Reads through the caller's unit of work so the check sees the same
    transaction as the operation it guards.
    """
    memberships = uow.organization_memberships.list_for_user(UserId.parse(user_id))
    organizations: dict[OrganizationId, Organization] = {}
    for membership in memberships:
        organization_id = membership.organization_id
        if organization_id in organizations:
            continue
        organization = uow.organizations.get(organization_id)
        if organization is not None:
            organizations[organization_id] = organization
    return evaluate_tenant_access(memberships=memberships, organizations=organizations)


def ensure_tenant_access(uow: UnitOfWork, *, user_id: str | UserId) -> TenantAccess:
    """Raise :class:`AuthenticationError` when the caller has lost tenant access.

    An unaffiliated caller is allowed through so a brand-new identity can create
    its first organization; that bootstrap path grants no authority inside any
    existing tenant.
    """
    access = resolve_tenant_access(uow, user_id=user_id)
    if access.status is TenantAccessStatus.DENIED:
        raise AuthenticationError(_DENIED_MESSAGE)
    return access


def actionable_organization_ids(
    uow: UnitOfWork, *, user_id: str | UserId
) -> frozenset[OrganizationId]:
    """The tenants the caller may currently act in.

    This is the single answer to "which tenants count for this caller", so
    tenant-scoped use cases stop re-deriving it from memberships and status
    themselves. A caller with no actionable tenant gets an empty set, never a
    broader one: the empty result is what makes a listing agree with the
    per-tenant reads that refuse a non-operational tenant outright.
    """
    return resolve_tenant_access(uow, user_id=user_id).organization_ids


__all__ = ["actionable_organization_ids", "ensure_tenant_access", "resolve_tenant_access"]
