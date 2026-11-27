"""Bearer-token tenant-access enforcement.

The OIDC identity provider authenticates a signed JWT, but a valid token is not
an entitlement. These tests sign real tokens with a real key and prove the same
tenant-access rule that guards browser sessions also guards the bearer path, so
a revoked membership cannot keep calling the API with a token it already holds.

Only the JWKS fetch is replaced (it is the one collaborator defined by a remote
server); signing, verification, and the membership decision are real code.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import AuthenticationError
from notes_service.domain.identity.entities import ExternalIdentity
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.organization.entities import Organization, OrganizationMembership
from notes_service.domain.organization.value_objects import MembershipStatus
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.identity.oidc_provider import OidcIdentityProvider

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)

ISSUER = "https://idp.example.com"
AUDIENCE = "notes"
SUBJECT = "subject-alice"


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _StaticJwks:
    """A JWKS client that always returns the test's public key."""

    def __init__(self, key: rsa.RSAPrivateKey) -> None:
        self._public = key.public_key()

    def get_signing_key_from_jwt(self, _token: str) -> object:
        return type("SigningKey", (), {"key": self._public})()


def _token(signing_key: rsa.RSAPrivateKey, *, subject: str = SUBJECT) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": subject,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
    )


def _provider(
    session_factory: sessionmaker[Session], signing_key: rsa.RSAPrivateKey
) -> OidcIdentityProvider:
    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    provider = OidcIdentityProvider(
        uow_factory=uow_factory,
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url="https://idp.example.com/jwks",
        algorithms=["RS256"],
    )
    provider._jwks_client = _StaticJwks(signing_key)  # type: ignore[assignment]
    return provider


def _link(session_factory: sessionmaker[Session], *, user_id: UserId) -> None:
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.external_identities.add(
            ExternalIdentity.link(user_id=user_id, provider="oidc", subject=SUBJECT)
        )
        uow.commit()


def _join(
    session_factory: sessionmaker[Session],
    *,
    user_id: UserId,
    status: MembershipStatus,
) -> Organization:
    organization = Organization.create(slug=f"org-{uuid.uuid4().hex[:8]}", name="Acme")
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.organizations.add(organization)
        uow.organization_memberships.add(
            OrganizationMembership.grant(
                organization_id=organization.id, user_id=user_id, status=status
            )
        )
        uow.commit()
    return organization


class _BearerContext:
    """The minimal AuthenticationContext the OIDC provider actually reads."""

    def __init__(self, token: str) -> None:
        self.authorization_header = f"Bearer {token}"

    demo_user_id_header: str | None = None
    session_cookie: str | None = None


class TestBearerTenantAccess:
    def test_active_member_is_authenticated(
        self, session_factory: sessionmaker[Session], signing_key: rsa.RSAPrivateKey
    ) -> None:
        _join(session_factory, user_id=ALICE, status=MembershipStatus.ACTIVE)
        _link(session_factory, user_id=ALICE)

        principal = _provider(session_factory, signing_key).authenticate(
            _BearerContext(_token(signing_key))
        )

        assert principal.user_id == ALICE

    def test_unaffiliated_user_is_authenticated_to_bootstrap(
        self, session_factory: sessionmaker[Session], signing_key: rsa.RSAPrivateKey
    ) -> None:
        """Consistent with the session path: no membership yet is allowed."""
        _link(session_factory, user_id=ALICE)

        principal = _provider(session_factory, signing_key).authenticate(
            _BearerContext(_token(signing_key))
        )

        assert principal.user_id == ALICE

    @pytest.mark.parametrize("status", [MembershipStatus.SUSPENDED, MembershipStatus.DEPROVISIONED])
    def test_inactive_member_is_rejected(
        self,
        session_factory: sessionmaker[Session],
        signing_key: rsa.RSAPrivateKey,
        status: MembershipStatus,
    ) -> None:
        _join(session_factory, user_id=ALICE, status=status)
        _link(session_factory, user_id=ALICE)

        with pytest.raises(AuthenticationError):
            _provider(session_factory, signing_key).authenticate(
                _BearerContext(_token(signing_key))
            )
