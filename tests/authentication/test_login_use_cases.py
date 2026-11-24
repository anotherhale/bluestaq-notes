"""OIDC login use-case tests.

These exercise the real ``SqlAlchemyUnitOfWork`` and the real domain aggregates;
only the relying party (the external HTTP/JWKS boundary) is a fake, because it is
the one collaborator whose behaviour is defined by a remote server. Everything
that decides *who may log in* is real code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import AuthenticationError
from notes_service.application.ports.oidc import VerifiedIdToken
from notes_service.application.use_cases.authentication import (
    BeginLogin,
    CompleteLogin,
    EndSession,
    ResolveSession,
)
from notes_service.domain.identity.entities import ExternalIdentity
from notes_service.domain.identity.value_objects import UserId, UserStatus
from notes_service.infrastructure.db.models import UserRow
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
DAVE = UserId(DEMO_USERS[3].id)  # seeded SUSPENDED

PROVIDER = "test-idp"


class FakeClock:
    """A clock the test moves by hand, so expiry is exercised deterministically."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


@dataclass
class FakeRelyingParty:
    """A relying party whose verification outcome the test controls.

    ``exchange_code`` is the only place the use case trusts an external result,
    so this is where a test says "the token verified as subject X" or "the
    exchange failed".
    """

    redirect_uri: str = "https://app.example.com/api/v1/auth/callback"
    subject: str = "subject-alice"
    provider_name: str = PROVIDER
    exchange_error: Exception | None = None
    last_code_verifier: str | None = None
    last_expected_nonce: str | None = None

    def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, login_hint: str | None = None
    ) -> str:
        return (
            f"https://idp.example.com/authorize?state={state}&nonce={nonce}"
            f"&code_challenge={code_challenge}"
        )

    def exchange_code(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> VerifiedIdToken:
        self.last_code_verifier = code_verifier
        self.last_expected_nonce = expected_nonce
        if self.exchange_error is not None:
            raise self.exchange_error
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        return VerifiedIdToken(
            issuer="https://idp.example.com",
            subject=self.subject,
            audience="notes",
            nonce=expected_nonce,
            email="alice@example.com",
            name="Alice",
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def relying_party() -> FakeRelyingParty:
    return FakeRelyingParty()


@pytest.fixture
def use_cases(
    session_factory: sessionmaker[Session], clock: FakeClock, relying_party: FakeRelyingParty
) -> dict[str, object]:
    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    def make(cls: type) -> object:  # type: ignore[type-arg]
        return cls(uow_factory=uow_factory, clock=clock, relying_party=relying_party)

    return {
        "begin": make(BeginLogin),
        "complete": make(CompleteLogin),
        "resolve": make(ResolveSession),
        "end": make(EndSession),
        "uow_factory": uow_factory,
    }


def link_identity(
    session_factory: sessionmaker[Session],
    *,
    user_id: UserId,
    subject: str,
    provider: str = PROVIDER,
) -> None:
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.external_identities.add(
            ExternalIdentity.link(user_id=user_id, provider=provider, subject=subject)
        )
        uow.commit()


def login(use_cases: dict[str, object]) -> str:
    """Run a full begin+complete login and return the raw session token."""
    begin = use_cases["begin"]
    complete = use_cases["complete"]
    request = begin.run()  # type: ignore[attr-defined]
    established = complete.run(state=request.state, code="auth-code")  # type: ignore[attr-defined]
    return established.session_token


class TestBeginLogin:
    def test_persists_single_use_transaction_before_redirect(
        self, use_cases: dict[str, object], session_factory: sessionmaker[Session]
    ) -> None:
        request = use_cases["begin"].run()  # type: ignore[attr-defined]

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            transaction = uow.login_transactions.get_by_state(request.state)
        assert transaction is not None
        assert transaction.consumed_at is None

    def test_authorization_url_carries_state_and_pkce_challenge(
        self, use_cases: dict[str, object], session_factory: sessionmaker[Session]
    ) -> None:
        request = use_cases["begin"].run()  # type: ignore[attr-defined]

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            transaction = uow.login_transactions.get_by_state(request.state)
        assert transaction is not None
        assert request.state in request.authorization_url
        assert transaction.code_challenge in request.authorization_url

    def test_each_begin_mints_distinct_state(self, use_cases: dict[str, object]) -> None:
        first = use_cases["begin"].run()  # type: ignore[attr-defined]
        second = use_cases["begin"].run()  # type: ignore[attr-defined]
        assert first.state != second.state


class TestCompleteLogin:
    def test_happy_path_establishes_session_and_principal(
        self, use_cases: dict[str, object], session_factory: sessionmaker[Session]
    ) -> None:
        link_identity(session_factory, user_id=ALICE, subject="subject-alice")

        request = use_cases["begin"].run()  # type: ignore[attr-defined]
        established = use_cases["complete"].run(state=request.state, code="code")  # type: ignore[attr-defined]

        assert established.session.user_id == ALICE
        assert established.principal.user_id == ALICE
        assert established.principal.provider == PROVIDER
        assert established.session_token

    def test_only_token_hash_is_persisted(
        self, use_cases: dict[str, object], session_factory: sessionmaker[Session]
    ) -> None:
        from notes_service.domain.identity.session import hash_session_token

        link_identity(session_factory, user_id=ALICE, subject="subject-alice")
        token = login(use_cases)

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            stored = uow.sessions.get_by_token_hash(hash_session_token(token))
            raw = uow.sessions.get_by_token_hash(token)
        assert stored is not None
        assert raw is None, "the raw cookie secret must never be the lookup key"

    def test_callback_passes_transaction_pkce_verifier_and_nonce(
        self,
        use_cases: dict[str, object],
        session_factory: sessionmaker[Session],
        relying_party: FakeRelyingParty,
    ) -> None:
        link_identity(session_factory, user_id=ALICE, subject="subject-alice")
        request = use_cases["begin"].run()  # type: ignore[attr-defined]

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            transaction = uow.login_transactions.get_by_state(request.state)
        assert transaction is not None

        use_cases["complete"].run(state=request.state, code="code")  # type: ignore[attr-defined]

        assert relying_party.last_code_verifier == transaction.code_verifier
        assert relying_party.last_expected_nonce == transaction.nonce

    def test_unknown_state_is_rejected(self, use_cases: dict[str, object]) -> None:
        with pytest.raises(AuthenticationError):
            use_cases["complete"].run(state="forged-state", code="code")  # type: ignore[attr-defined]

    def test_missing_parameters_rejected(self, use_cases: dict[str, object]) -> None:
        with pytest.raises(AuthenticationError):
            use_cases["complete"].run(state="", code="code")  # type: ignore[attr-defined]
        with pytest.raises(AuthenticationError):
            use_cases["complete"].run(state="state", code="")  # type: ignore[attr-defined]

    def test_replayed_callback_is_rejected(
        self, use_cases: dict[str, object], session_factory: sessionmaker[Session]
    ) -> None:
        link_identity(session_factory, user_id=ALICE, subject="subject-alice")
        request = use_cases["begin"].run()  # type: ignore[attr-defined]
        use_cases["complete"].run(state=request.state, code="code")  # type: ignore[attr-defined]

        with pytest.raises(AuthenticationError):
            use_cases["complete"].run(state=request.state, code="code")  # type: ignore[attr-defined]

    def test_expired_transaction_is_rejected(
        self,
        use_cases: dict[str, object],
        session_factory: sessionmaker[Session],
        clock: FakeClock,
    ) -> None:
        link_identity(session_factory, user_id=ALICE, subject="subject-alice")
        request = use_cases["begin"].run()  # type: ignore[attr-defined]
        clock.advance(timedelta(minutes=6))  # LOGIN_TRANSACTION_TTL is 5 minutes

        with pytest.raises(AuthenticationError):
            use_cases["complete"].run(state=request.state, code="code")  # type: ignore[attr-defined]

    def test_unlinked_subject_is_denied_no_auto_provisioning(
        self, use_cases: dict[str, object], session_factory: sessionmaker[Session]
    ) -> None:
        request = use_cases["begin"].run()  # type: ignore[attr-defined]

        with pytest.raises(AuthenticationError):
            use_cases["complete"].run(state=request.state, code="code")  # type: ignore[attr-defined]

        # The failed attempt must not have created a session.
        with SqlAlchemyUnitOfWork(session_factory) as uow:
            assert uow.sessions.list_active_for_user(ALICE, now=datetime.now(UTC)) == []

    def test_inactive_linked_user_is_denied(
        self,
        use_cases: dict[str, object],
        session_factory: sessionmaker[Session],
        relying_party: FakeRelyingParty,
    ) -> None:
        link_identity(session_factory, user_id=DAVE, subject="subject-dave")
        relying_party.subject = "subject-dave"
        request = use_cases["begin"].run()  # type: ignore[attr-defined]

        with pytest.raises(AuthenticationError):
            use_cases["complete"].run(state=request.state, code="code")  # type: ignore[attr-defined]

    def test_exchange_failure_denies_and_leaves_no_session(
        self,
        use_cases: dict[str, object],
        session_factory: sessionmaker[Session],
        relying_party: FakeRelyingParty,
    ) -> None:
        link_identity(session_factory, user_id=ALICE, subject="subject-alice")
        relying_party.exchange_error = AuthenticationError("token verification failed")
        request = use_cases["begin"].run()  # type: ignore[attr-defined]

        with pytest.raises(AuthenticationError):
            use_cases["complete"].run(state=request.state, code="code")  # type: ignore[attr-defined]

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            assert uow.sessions.list_active_for_user(ALICE, now=datetime.now(UTC)) == []


class TestResolveSession:
    def test_valid_token_yields_principal(
        self, use_cases: dict[str, object], session_factory: sessionmaker[Session]
    ) -> None:
        link_identity(session_factory, user_id=ALICE, subject="subject-alice")
        token = login(use_cases)

        principal = use_cases["resolve"].run(session_token=token)  # type: ignore[attr-defined]
        assert principal.user_id == ALICE

    def test_empty_token_rejected(self, use_cases: dict[str, object]) -> None:
        with pytest.raises(AuthenticationError):
            use_cases["resolve"].run(session_token="")  # type: ignore[attr-defined]

    def test_unknown_token_rejected(self, use_cases: dict[str, object]) -> None:
        with pytest.raises(AuthenticationError):
            use_cases["resolve"].run(session_token="not-a-real-token")  # type: ignore[attr-defined]

    def test_idle_expiry_rejects(
        self,
        use_cases: dict[str, object],
        session_factory: sessionmaker[Session],
        clock: FakeClock,
    ) -> None:
        link_identity(session_factory, user_id=ALICE, subject="subject-alice")
        token = login(use_cases)
        clock.advance(timedelta(hours=9))  # idle TTL is 8 hours

        with pytest.raises(AuthenticationError):
            use_cases["resolve"].run(session_token=token)  # type: ignore[attr-defined]

    def test_resolution_slides_idle_window(
        self,
        use_cases: dict[str, object],
        session_factory: sessionmaker[Session],
        clock: FakeClock,
    ) -> None:
        from notes_service.domain.identity.session import hash_session_token

        link_identity(session_factory, user_id=ALICE, subject="subject-alice")
        token = login(use_cases)

        clock.advance(timedelta(hours=6))
        use_cases["resolve"].run(session_token=token)  # type: ignore[attr-defined]

        # Another 6 hours would have exceeded the original idle deadline; the
        # touch on the previous call must have pushed it forward.
        clock.advance(timedelta(hours=6))
        use_cases["resolve"].run(session_token=token)  # type: ignore[attr-defined]

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            session = uow.sessions.get_by_token_hash(hash_session_token(token))
        assert session is not None
        assert session.last_seen_at == clock.now()

    def test_inactive_owner_rejected(
        self,
        use_cases: dict[str, object],
        session_factory: sessionmaker[Session],
    ) -> None:
        link_identity(session_factory, user_id=ALICE, subject="subject-alice")
        token = login(use_cases)

        # Suspend Alice after her session was established. The user repository
        # is read-only, so the account lifecycle is changed at the row level.
        with session_factory() as session:
            row = session.get(UserRow, uuid.UUID(str(ALICE)))
            assert row is not None
            row.status = UserStatus.SUSPENDED.value
            session.commit()

        with pytest.raises(AuthenticationError):
            use_cases["resolve"].run(session_token=token)  # type: ignore[attr-defined]


class TestEndSession:
    def test_logout_revokes_session(
        self, use_cases: dict[str, object], session_factory: sessionmaker[Session]
    ) -> None:
        link_identity(session_factory, user_id=ALICE, subject="subject-alice")
        token = login(use_cases)

        use_cases["end"].run(session_token=token)  # type: ignore[attr-defined]

        with pytest.raises(AuthenticationError):
            use_cases["resolve"].run(session_token=token)  # type: ignore[attr-defined]

    def test_logout_is_idempotent_and_silent(
        self, use_cases: dict[str, object], session_factory: sessionmaker[Session]
    ) -> None:
        link_identity(session_factory, user_id=ALICE, subject="subject-alice")
        token = login(use_cases)

        use_cases["end"].run(session_token=token)  # type: ignore[attr-defined]
        use_cases["end"].run(session_token=token)  # type: ignore[attr-defined]

    def test_logout_of_unknown_token_is_silent(self, use_cases: dict[str, object]) -> None:
        use_cases["end"].run(session_token="never-issued")  # type: ignore[attr-defined]
        use_cases["end"].run(session_token="")  # type: ignore[attr-defined]
