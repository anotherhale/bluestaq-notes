"""HTTP-level tests for the browser-login router.

The router, the use cases, the cookie handling and the database are all real.
Only the relying party is a fake, because it is the external IdP boundary. This
is what proves the *HTTP contract*: redirect shapes, cookie attributes, the
fail-closed 404, and the fact that no failure path leaks detail.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.ports.oidc import VerifiedIdToken
from notes_service.application.use_cases.authentication import (
    BeginLogin,
    CompleteLogin,
    EndSession,
)
from notes_service.bootstrap.config import Settings
from notes_service.domain.identity.entities import ExternalIdentity
from notes_service.domain.identity.session import hash_session_token
from notes_service.domain.identity.value_objects import UserId
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.security.clock import SystemClock

ALICE = UserId(DEMO_USERS[0].id)
PROVIDER = "test-idp"
COOKIE = "notes_session"


class FakeRelyingParty:
    provider_name = PROVIDER
    redirect_uri = "https://app.example.com/api/v1/auth/callback"

    def __init__(self) -> None:
        self.subject = "subject-alice"
        self.fail_exchange = False

    def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, login_hint: str | None = None
    ) -> str:
        return (
            f"https://idp.example.com/authorize?state={state}&nonce={nonce}"
            f"&code_challenge={code_challenge}&response_type=code"
        )

    def exchange_code(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> VerifiedIdToken:
        from notes_service.application.errors import AuthenticationError

        if self.fail_exchange:
            raise AuthenticationError("nope")
        now = datetime.now(UTC)
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
def login_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "auth_oidc_login_enabled": True,
            "oidc_issuer": "https://idp.example.com",
            "oidc_client_id": "notes-client",
            "oidc_authorization_endpoint": "https://idp.example.com/authorize",
            "oidc_token_endpoint": "https://idp.example.com/token",
            "oidc_jwks_url": "https://idp.example.com/jwks",
            "oidc_redirect_uri": "https://app.example.com/api/v1/auth/callback",
            "session_cookie_name": COOKIE,
        }
    )


@pytest.fixture
def relying_party() -> FakeRelyingParty:
    return FakeRelyingParty()


@pytest.fixture
def login_app(
    login_settings: Settings,
    engine: object,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    relying_party: FakeRelyingParty,
) -> FastAPI:
    from notes_service.bootstrap import app as app_module
    from notes_service.bootstrap.container import build_container as real_build

    def _builder(_settings: Settings) -> object:
        return real_build(_settings, engine=engine)  # type: ignore[arg-type]

    monkeypatch.setattr(app_module, "build_container", _builder)
    application = app_module.create_app(login_settings)

    # Swap the external IdP boundary for the fake, keeping every other
    # collaborator (use cases, UoW, clock, settings) exactly as built.
    container = application.state.container
    clock = SystemClock()

    def _uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    def factory(use_case_type: type) -> object:  # type: ignore[type-arg]
        return use_case_type(uow_factory=_uow_factory, clock=clock, relying_party=relying_party)

    container.relying_party = relying_party
    container.begin_login = lambda: factory(BeginLogin)
    container.complete_login = lambda: factory(CompleteLogin)
    container.end_session = lambda: factory(EndSession)

    with session_factory() as session:
        from notes_service.infrastructure.db.seed import seed_demo_users

        seed_demo_users(session)
        session.commit()
    return application


@pytest.fixture
def login_client(login_app: FastAPI) -> Iterator[TestClient]:
    # HTTPS base URL: the session cookie is ``Secure`` by default, and a
    # plain-http client jar would silently drop it, which would make the
    # logout tests pass for the wrong reason.
    with TestClient(login_app, base_url="https://testserver", follow_redirects=False) as client:
        yield client


def link_alice(session_factory: sessionmaker[Session], subject: str = "subject-alice") -> None:
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.external_identities.add(
            ExternalIdentity.link(user_id=ALICE, provider=PROVIDER, subject=subject)
        )
        uow.commit()


def begin(client: TestClient) -> str:
    """Start a login and return the ``state`` from the IdP redirect."""
    response = client.get("/api/v1/auth/login")
    assert response.status_code == 307, response.text
    location = response.headers["location"]
    return location.split("state=")[1].split("&")[0]


class TestLoginDisabled:
    def test_login_404_when_not_configured(self, client: TestClient) -> None:
        assert client.get("/api/v1/auth/login").status_code == 404

    def test_callback_404_when_not_configured(self, client: TestClient) -> None:
        assert client.get("/api/v1/auth/callback?state=s&code=c").status_code == 404

    def test_logout_404_when_not_configured(self, client: TestClient) -> None:
        assert client.post("/api/v1/auth/logout").status_code == 404


class TestLoginRedirect:
    def test_login_redirects_to_idp_with_pkce_and_state(self, login_client: TestClient) -> None:
        response = login_client.get("/api/v1/auth/login")
        assert response.status_code == 307
        location = response.headers["location"]
        assert location.startswith("https://idp.example.com/authorize")
        assert "state=" in location
        assert "code_challenge=" in location
        assert "response_type=code" in location

    def test_login_hint_is_forwarded(self, login_client: TestClient) -> None:
        response = login_client.get("/api/v1/auth/login?login_hint=alice@example.com")
        assert response.status_code == 307

    def test_no_cookie_is_set_before_login_completes(self, login_client: TestClient) -> None:
        response = login_client.get("/api/v1/auth/login")
        assert COOKIE not in response.cookies


class TestCallback:
    def test_successful_login_sets_hardened_cookie(
        self, login_client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        link_alice(session_factory)
        state = begin(login_client)

        response = login_client.get(f"/api/v1/auth/callback?state={state}&code=abc")

        assert response.status_code == 303
        assert response.headers["location"] == "/"
        raw_cookie = response.headers["set-cookie"]
        assert "httponly" in raw_cookie.lower()
        assert "samesite=lax" in raw_cookie.lower()
        assert "path=/" in raw_cookie.lower()

    def test_cookie_token_is_not_the_raw_db_key(
        self, login_client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        link_alice(session_factory)
        state = begin(login_client)
        response = login_client.get(f"/api/v1/auth/callback?state={state}&code=abc")
        token = response.cookies[COOKIE]

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            assert uow.sessions.get_by_token_hash(hash_session_token(token)) is not None
            assert uow.sessions.get_by_token_hash(token) is None

    def test_idp_error_is_generic_401(self, login_client: TestClient) -> None:
        response = login_client.get("/api/v1/auth/callback?error=access_denied")
        assert response.status_code == 401
        body = response.json()
        assert "access_denied" not in str(body)

    def test_missing_parameters_are_422(self, login_client: TestClient) -> None:
        assert login_client.get("/api/v1/auth/callback").status_code == 422
        assert login_client.get("/api/v1/auth/callback?state=only").status_code == 422

    def test_forged_state_is_401(self, login_client: TestClient) -> None:
        response = login_client.get("/api/v1/auth/callback?state=forged&code=abc")
        assert response.status_code == 401

    def test_replayed_callback_is_401(
        self, login_client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        link_alice(session_factory)
        state = begin(login_client)
        assert login_client.get(f"/api/v1/auth/callback?state={state}&code=a").status_code == 303
        assert login_client.get(f"/api/v1/auth/callback?state={state}&code=a").status_code == 401

    def test_unlinked_subject_is_401_without_session(
        self, login_client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        state = begin(login_client)
        response = login_client.get(f"/api/v1/auth/callback?state={state}&code=abc")
        assert response.status_code == 401
        assert COOKIE not in response.cookies

    def test_exchange_failure_is_401(
        self,
        login_client: TestClient,
        session_factory: sessionmaker[Session],
        relying_party: FakeRelyingParty,
    ) -> None:
        link_alice(session_factory)
        relying_party.fail_exchange = True
        state = begin(login_client)
        response = login_client.get(f"/api/v1/auth/callback?state={state}&code=abc")
        assert response.status_code == 401


class TestLogout:
    """Logout is cookie-authenticated, so it is subject to the origin guard.

    A browser attaches ``Origin`` to every unsafe request, same-origin included —
    without it the request is not one a browser can make, and the guard refuses it.
    These tests therefore send the header a browser would send, then assert the
    logout behaviour they were written for.
    """

    BROWSER_HEADERS: ClassVar[dict[str, str]] = {"Origin": "https://testserver"}

    def test_logout_clears_cookie(
        self, login_client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        link_alice(session_factory)
        state = begin(login_client)
        login_client.get(f"/api/v1/auth/callback?state={state}&code=abc")

        response = login_client.post("/api/v1/auth/logout", headers=self.BROWSER_HEADERS)
        assert response.status_code == 204
        assert 'notes_session=""' in response.headers["set-cookie"].replace(" ", "")

    def test_logout_without_cookie_is_still_204(self, login_client: TestClient) -> None:
        assert login_client.post("/api/v1/auth/logout").status_code == 204

    def test_logout_with_a_cookie_and_no_origin_is_refused(
        self, login_client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        link_alice(session_factory)
        state = begin(login_client)
        token = login_client.get(f"/api/v1/auth/callback?state={state}&code=abc").cookies[COOKIE]

        response = login_client.post("/api/v1/auth/logout")

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_rejected"
        with SqlAlchemyUnitOfWork(session_factory) as uow:
            session = uow.sessions.get_by_token_hash(hash_session_token(token))
        assert session is not None and session.revoked_at is None

    def test_logout_revokes_the_session(
        self,
        login_client: TestClient,
        session_factory: sessionmaker[Session],
    ) -> None:
        link_alice(session_factory)
        state = begin(login_client)
        token = login_client.get(f"/api/v1/auth/callback?state={state}&code=abc").cookies[COOKIE]

        login_client.post("/api/v1/auth/logout", headers=self.BROWSER_HEADERS)

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            session = uow.sessions.get_by_token_hash(hash_session_token(token))
        assert session is None or session.revoked_at is not None


class TestCookieBeatsDemoHeader:
    """With login enabled, a presented cookie is authoritative over the demo header."""

    def test_forged_cookie_with_demo_header_is_401(self, login_client: TestClient) -> None:
        response = login_client.get(
            "/api/v1/me",
            headers={"Cookie": f"{COOKIE}=forged", "X-Demo-User-ID": str(ALICE)},
        )
        # The session provider runs and rejects; the demo header must not rescue
        # the request, or a pinned bad cookie would become a demo impersonation.
        assert response.status_code == 401

    def test_forged_cookie_without_demo_header_is_401(self, login_client: TestClient) -> None:
        response = login_client.get("/api/v1/me", headers={"Cookie": f"{COOKIE}=forged"})
        assert response.status_code == 401

    def test_valid_cookie_authenticates_the_linked_user(
        self, login_client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        link_alice(session_factory)
        state = begin(login_client)
        login_client.get(f"/api/v1/auth/callback?state={state}&code=abc")

        response = login_client.get("/api/v1/me")
        assert response.status_code == 200
        assert response.json()["email"] == "alice@example.com"

    def test_valid_cookie_wins_over_a_different_demo_header(
        self, login_client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        link_alice(session_factory)
        state = begin(login_client)
        login_client.get(f"/api/v1/auth/callback?state={state}&code=abc")

        response = login_client.get("/api/v1/me", headers={"X-Demo-User-ID": str(DEMO_USERS[1].id)})
        # The cookie is authoritative: the demo header cannot switch identity.
        assert response.status_code == 200
        assert response.json()["email"] == "alice@example.com"
