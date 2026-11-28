"""Identity linking: the write path that makes OIDC login possible (NEX-091).

These run the real adapters, the real unit of work and the real authorization
providers against a seeded SQLite database. Nothing is mocked: the properties
under test (tenant isolation, escalation refusal, targeted session revocation)
only exist when the whole slice is wired together.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import AccessDenied, InvalidInput, ResourceNotFound
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.identity_linking import (
    LinkExternalIdentity,
    ListUserExternalIdentities,
    UnlinkExternalIdentity,
)
from notes_service.application.use_cases.organization import (
    CreateOrganization,
    InviteOrganizationMember,
)
from notes_service.application.use_cases.organization_policy import (
    DefaultOrganizationAuthorizationProvider,
)
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.session import AuthenticationAssurance, SessionId, UserSession
from notes_service.domain.identity.value_objects import Email, ExternalIdentityId, UserId
from notes_service.domain.organization.value_objects import OrganizationRole
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
    """Alice owns the org; Bob is a plain member; Carol is not a member."""
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


def _pairs(identities):  # type: ignore[no-untyped-def]
    """(provider, subject) pairs for an identity list.

    The seed links every demo user to the ``demo`` provider, so assertions must
    name the identities they care about rather than comparing whole lists.
    """
    return sorted((i.provider, i.subject) for i in identities)


def _link(harness: Harness, org_id, user_id, provider="okta", subject="sub-1"):  # type: ignore[no-untyped-def]
    return harness.make(LinkExternalIdentity).run(
        principal_for(ALICE),
        organization_id=org_id,
        user_id=user_id,
        provider=provider,
        subject=subject,
    )


class TestLink:
    def test_admin_links_member_identity(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        identity = _link(harness, org.organization.id, BOB)
        assert identity.user_id == BOB
        assert identity.provider == "okta"
        assert identity.subject == "sub-1"
        # And it is discoverable on the same slice that wrote it.
        listed = harness.make(ListUserExternalIdentities).run(
            principal_for(ALICE), organization_id=org.organization.id, user_id=BOB
        )
        assert identity.id in {i.id for i in listed}
        assert ("okta", "sub-1") in _pairs(listed)

    def test_relink_same_pair_is_idempotent(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        first = _link(harness, org.organization.id, BOB)
        second = _link(harness, org.organization.id, BOB)
        assert first.id == second.id

    def test_cannot_rebind_pair_bound_to_another_account(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        harness.make(InviteOrganizationMember).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            user_id=CAROL,
            role=OrganizationRole.ORG_MEMBER,
        )
        _link(harness, org.organization.id, BOB, subject="shared")
        with pytest.raises(InvalidInput):
            _link(harness, org.organization.id, CAROL, subject="shared")

    def test_non_member_target_is_not_found(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        """A user outside the tenant must be indistinguishable from an absent one."""
        with pytest.raises(ResourceNotFound):
            _link(harness, org.organization.id, CAROL)

    def test_plain_member_cannot_link(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(AccessDenied):
            harness.make(LinkExternalIdentity).run(
                principal_for(BOB),
                organization_id=org.organization.id,
                user_id=CAROL,
                provider="okta",
                subject="x",
            )

    def test_admin_in_other_tenant_cannot_link(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        other = harness.make(CreateOrganization).run(
            principal_for(CAROL), name="Other", slug="other"
        )
        # Carol owns a different tenant, so Acme's members are 404 to her.
        with pytest.raises(ResourceNotFound):
            harness.make(LinkExternalIdentity).run(
                principal_for(CAROL),
                organization_id=org.organization.id,
                user_id=BOB,
                provider="okta",
                subject="x",
            )
        assert other.organization.id != org.organization.id

    def test_admin_cannot_link_more_privileged_account(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        """An admin must not be able to mint a login for the owner's account."""
        harness.make(InviteOrganizationMember).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            user_id=CAROL,
            role=OrganizationRole.ORG_ADMIN,
        )
        with pytest.raises(AccessDenied):
            harness.make(LinkExternalIdentity).run(
                principal_for(CAROL),
                organization_id=org.organization.id,
                user_id=ALICE,
                provider="okta",
                subject="owner-takeover",
            )


class TestUnlink:
    def _session(self, user_id: UserId, *, provider: str, subject: str) -> UserSession:
        now = datetime.now(UTC)
        return UserSession(
            id=SessionId.new(),
            user_id=user_id,
            provider=provider,
            subject=subject,
            # The table checks this is a 64-char SHA-256 hex digest, the same
            # shape production writes, so a fake here would be rejected.
            token_hash=hashlib.sha256(str(SessionId.new()).encode()).hexdigest(),
            assurance=AuthenticationAssurance.SINGLE_FACTOR,
            idle_expires_at=now + timedelta(hours=1),
            absolute_expires_at=now + timedelta(hours=8),
        )

    def test_unlink_removes_link_and_revokes_that_identitys_sessions(
        self, harness: Harness, org
    ) -> None:  # type: ignore[no-untyped-def]
        linked = _link(harness, org.organization.id, BOB, subject="sub-1")
        with harness.uow() as uow:
            uow.sessions.add(self._session(BOB, provider="okta", subject="sub-1"))
            # A session from a *different* provider must survive the unlink.
            uow.sessions.add(self._session(BOB, provider="demo", subject=str(BOB)))
            uow.commit()

        revoked = harness.make(UnlinkExternalIdentity).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            user_id=BOB,
            identity_id=linked.id,
        )
        assert revoked == 1

        with harness.uow() as uow:
            now = datetime.now(UTC)
            live = uow.sessions.list_active_for_user(BOB, now=now)
            # The okta session is gone; the demo one was never touched.
            assert [s.provider for s in live] == ["demo"]
            assert _pairs(uow.external_identities.list_for_user(BOB)) == [("demo", str(BOB))]

    def test_unlink_unknown_link_is_not_found(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(ResourceNotFound):
            harness.make(UnlinkExternalIdentity).run(
                principal_for(ALICE),
                organization_id=org.organization.id,
                user_id=BOB,
                identity_id=ExternalIdentityId.new(),
            )

    def test_cannot_unlink_another_users_link(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        """Bob's link must not be removable by addressing Carol."""
        harness.make(InviteOrganizationMember).run(
            principal_for(ALICE),
            organization_id=org.organization.id,
            user_id=CAROL,
            role=OrganizationRole.ORG_MEMBER,
        )
        bob_link = _link(harness, org.organization.id, BOB, subject="bob-sub")
        with pytest.raises(ResourceNotFound):
            harness.make(UnlinkExternalIdentity).run(
                principal_for(ALICE),
                organization_id=org.organization.id,
                user_id=CAROL,
                identity_id=bob_link.id,
            )
        # Bob's link is untouched.
        listed = harness.make(ListUserExternalIdentities).run(
            principal_for(ALICE), organization_id=org.organization.id, user_id=BOB
        )
        assert ("okta", "bob-sub") in _pairs(listed)


class TestList:
    def test_list_requires_membership_visibility(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(ResourceNotFound):
            harness.make(ListUserExternalIdentities).run(
                principal_for(CAROL), organization_id=org.organization.id, user_id=BOB
            )

    def test_list_non_member_target_is_not_found(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(ResourceNotFound):
            harness.make(ListUserExternalIdentities).run(
                principal_for(ALICE), organization_id=org.organization.id, user_id=CAROL
            )

    def test_list_own_links_visible_to_owner(self, harness: Harness, org) -> None:  # type: ignore[no-untyped-def]
        _link(harness, org.organization.id, ALICE, provider="okta", subject="alice-1")
        _link(harness, org.organization.id, ALICE, provider="azure", subject="alice-2")
        listed = harness.make(ListUserExternalIdentities).run(
            principal_for(ALICE), organization_id=org.organization.id, user_id=ALICE
        )
        assert _pairs(listed) == [
            ("azure", "alice-2"),
            ("demo", str(ALICE)),
            ("okta", "alice-1"),
        ]
