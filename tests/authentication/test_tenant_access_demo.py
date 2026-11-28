"""Demo-identity tenant-access enforcement.

The demo provider is a *fifth* way to establish an identity, alongside the OIDC
login callback, per-request session resolution, the bearer provider, and agent
token resolution. ``application/use_cases/tenant_access.py`` claims the check is
shared by "every path that establishes or restores an identity", and its logic
is what makes a membership revocation take effect immediately rather than at
session expiry.

A demo header is not an entitlement either. Without this guard a suspended or
deprovisioned membership could keep calling the API in development, which is
exactly the offboarding gap the other four paths close. These tests use only the
seeded demo users and the real HTTP stack.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from notes_service.domain.organization.entities import Organization, OrganizationMembership
from notes_service.domain.organization.value_objects import MembershipStatus, OrganizationStatus
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from tests.conftest import ALICE, auth_headers

pytestmark = pytest.mark.usefixtures("seeded")


def _give_membership(
    session_factory: sessionmaker[Session], *, status: MembershipStatus
) -> Organization:
    organization = Organization.create(slug="acme", name="Acme")
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.organizations.add(organization)
        uow.organization_memberships.add(
            OrganizationMembership.grant(
                organization_id=organization.id,
                user_id=ALICE,  # type: ignore[arg-type]
                status=status,
            )
        )
        uow.commit()
    return organization


def _deprovision(session_factory: sessionmaker[Session], organization: Organization) -> None:
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        membership = uow.organization_memberships.get_for_user(organization.id, ALICE)
        assert membership is not None
        membership.deprovision()
        uow.organization_memberships.save(membership)
        uow.commit()


class TestDemoIdentityTenantAccess:
    def test_active_membership_still_authenticates(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        _give_membership(session_factory, status=MembershipStatus.ACTIVE)
        assert client.get("/api/v1/me", headers=auth_headers(ALICE)).status_code == 200

    def test_deprovisioned_membership_cannot_authenticate(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        """The core property: a revoked membership stops demo auth immediately."""
        organization = _give_membership(session_factory, status=MembershipStatus.ACTIVE)

        # Works while the membership is active...
        assert client.get("/api/v1/me", headers=auth_headers(ALICE)).status_code == 200

        # ...and stops the moment it is revoked.
        _deprovision(session_factory, organization)

        response = client.get("/api/v1/me", headers=auth_headers(ALICE))
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    @pytest.mark.parametrize("status", [MembershipStatus.SUSPENDED, MembershipStatus.DEPROVISIONED])
    def test_inactive_membership_cannot_authenticate(
        self,
        client: TestClient,
        session_factory: sessionmaker[Session],
        status: MembershipStatus,
    ) -> None:
        _give_membership(session_factory, status=status)
        assert client.get("/api/v1/me", headers=auth_headers(ALICE)).status_code == 401

    def test_suspended_organization_blocks_authentication(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        organization = _give_membership(session_factory, status=MembershipStatus.ACTIVE)
        with SqlAlchemyUnitOfWork(session_factory) as uow:
            loaded = uow.organizations.get(organization.id)
            assert loaded is not None
            loaded.change_status(status=OrganizationStatus.SUSPENDED)
            uow.organizations.save(loaded)
            uow.commit()

        assert client.get("/api/v1/me", headers=auth_headers(ALICE)).status_code == 401

    def test_unaffiliated_user_still_authenticates(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        """A brand-new identity with no membership must still be able to bootstrap."""
        _ = session_factory  # no membership is created for ALICE
        assert client.get("/api/v1/me", headers=auth_headers(ALICE)).status_code == 200
