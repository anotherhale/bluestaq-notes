"""Cross-site request forgery defence for cookie-authenticated requests.

The gap these tests close is concrete rather than theoretical: browser login
(NEX-002) put a session credential in a cookie, and ``SameSite=Lax`` was the only
thing standing between that cookie and a write issued by another origin. A
``SameSite`` attribute is a browser default, not an enforcement point — a
deployment that needs a cross-origin frontend must set ``SameSite=None``, at
which point nothing server-side remained. ``docs/SECURITY.md`` said CSRF was "not
applicable because the API is token/header-based", which stopped being true the
moment a cookie existed.

The app, the middleware, the session cookie and the database are all real here.
The ``Origin``/``Host`` headers are what a browser would send. Two layers are
proven separately:

* the **pure decision** (:func:`notes_service.presentation.csrf.evaluate`), so
  the rule can be read and attacked without HTTP; and
* the **wired app**, so a passing unit test cannot coexist with a guard that was
  never registered or runs in the wrong order relative to CORS.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from notes_service.bootstrap.config import Settings
from notes_service.domain.identity.session import UserSession
from notes_service.domain.identity.value_objects import UserId
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.presentation.csrf import (
    SAFE_METHODS,
    evaluate,
    origin_is_allowed,
    request_host_port,
    request_scheme,
)

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
COOKIE = "notes_session"
SITE = "https://app.example.com"


def cookie_header(token: str) -> dict[str, str]:
    return {"cookie": f"{COOKIE}={token}"}


def session_factory_for(engine: object) -> sessionmaker[Session]:
    from sqlalchemy.orm import sessionmaker as _sessionmaker

    return _sessionmaker(bind=engine, expire_on_commit=False, future=True)  # type: ignore[arg-type]


@pytest.fixture
def live_session(engine: object, session_factory: sessionmaker[Session]) -> Iterator[str]:
    """Persist a real session for Alice and yield the raw cookie secret.

    The guard must fire on a *cookie*, so the tests present a session the
    identity provider would actually accept. A fabricated token would prove the
    guard blocks an unauthenticated request, which is not the thing at risk.
    """
    token = "csrf-test-session-token-value"
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.sessions.add(
            UserSession.establish(
                user_id=ALICE,
                provider="test-idp",
                subject="subject-alice",
                token=token,
                idle_ttl=timedelta(hours=1),
                absolute_ttl=timedelta(days=1),
            )
        )
        uow.commit()
    yield token


class TestSafeMethodsAreNeverBlocked:
    """A safe method must not change state, so it needs no origin check."""

    @pytest.mark.parametrize("method", sorted(SAFE_METHODS))
    def test_safe_methods_are_allowed_with_a_cookie_and_no_origin(self, method: str) -> None:
        decision = evaluate(
            method=method,
            cookie_present=True,
            origin=None,
            request_host=("app.example.com", "443"),
            request_scheme="https",
            trusted_origins=[],
        )
        assert decision.allowed
        assert decision.reason == "safe-method"

    def test_unknown_method_is_treated_as_unsafe(self) -> None:
        # A method this build has never seen must be guarded, not assumed safe.
        decision = evaluate(
            method="PROPFIND",
            cookie_present=True,
            origin=None,
            request_host=("app.example.com", "443"),
            request_scheme="https",
            trusted_origins=[],
        )
        assert not decision.allowed


class TestNonCookieCallersAreNotBlocked:
    """A bearer or demo caller rides no ambient authority, so nothing is forgeable."""

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_unsafe_method_without_a_cookie_is_allowed(self, method: str) -> None:
        decision = evaluate(
            method=method,
            cookie_present=False,
            origin=None,
            request_host=("app.example.com", "443"),
            request_scheme="https",
            trusted_origins=[],
        )
        assert decision.allowed
        assert decision.reason == "not-cookie-authenticated"


class TestCookieAuthenticatedOriginChecks:
    """The core rule: a cookie plus an unsafe method requires a trusted origin."""

    def decide(self, origin: str | None, *, host: tuple[str, str] | None = None) -> bool:
        return evaluate(
            method="POST",
            cookie_present=True,
            origin=origin,
            request_host=host or ("app.example.com", "443"),
            request_scheme="https",
            trusted_origins=[],
        ).allowed

    def test_same_origin_is_allowed(self) -> None:
        assert self.decide(SITE)

    def test_default_port_and_explicit_port_compare_equal(self) -> None:
        assert self.decide("https://app.example.com:443")

    def test_foreign_origin_is_rejected(self) -> None:
        assert not self.decide("https://evil.example")

    def test_lookalike_suffix_is_rejected(self) -> None:
        # `app.example.com.evil.com` must not match `app.example.com`.
        assert not self.decide("https://app.example.com.evil.com")

    def test_missing_origin_is_rejected(self) -> None:
        # Fail closed: silence is not consent. Browsers send Origin on every
        # unsafe request, so a real browser client never hits this.
        assert not self.decide(None)

    def test_empty_origin_is_rejected(self) -> None:
        assert not self.decide("")

    def test_opaque_null_origin_is_rejected(self) -> None:
        # A sandboxed iframe sends `Origin: null`; it must not pass.
        assert not self.decide("null")

    def test_scheme_downgrade_is_rejected(self) -> None:
        assert not self.decide("http://app.example.com")

    def test_port_mismatch_is_rejected(self) -> None:
        assert not self.decide("https://app.example.com:8443")

    def test_origin_with_a_path_is_rejected(self) -> None:
        # An Origin header never carries a path; a value that does is not one.
        assert not self.decide("https://app.example.com/api/v1/notes")


class TestOriginMatchingMechanics:
    def test_trusted_origin_is_allowed_even_when_cross_host(self) -> None:
        assert origin_is_allowed(
            origin="https://frontend.example",
            request_host=("api.example", "443"),
            request_scheme="https",
            trusted_origins=["https://frontend.example"],
        )

    def test_trusted_origin_does_not_widen_to_a_sibling(self) -> None:
        assert not origin_is_allowed(
            origin="https://frontend.example.evil",
            request_host=("api.example", "443"),
            request_scheme="https",
            trusted_origins=["https://frontend.example"],
        )

    def test_request_without_host_cannot_match_same_origin(self) -> None:
        assert not origin_is_allowed(
            origin=SITE,
            request_host=None,
            request_scheme="https",
            trusted_origins=[],
        )

    def test_no_origin_is_never_allowed(self) -> None:
        assert not origin_is_allowed(
            origin=None,
            request_host=("app.example.com", "443"),
            request_scheme="https",
            trusted_origins=[SITE],
        )


class TestProxyHeaders:
    """A TLS-terminating proxy must not make an https origin look foreign."""

    def test_garbage_host_header_yields_no_host_port(self, settings: Settings) -> None:
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"host", b"not a host:port:extra")],
            "scheme": "https",
            "server": ("app.example.com", 443),
            "path": "/",
            "query_string": b"",
        }
        assert request_host_port(Request(scope)) is None  # type: ignore[arg-type]

    def test_forwarded_proto_is_authoritative(self) -> None:
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"host", b"app.example.com"), (b"x-forwarded-proto", b"https")],
            "scheme": "http",
            "server": ("app.example.com", 80),
            "path": "/",
            "query_string": b"",
        }
        assert request_scheme(Request(scope)) == "https"  # type: ignore[arg-type]


class TestWiredApp:
    """The middleware is registered, and refuses before CORS answers."""

    @pytest.fixture
    def cookie_client(
        self,
        settings: Settings,
        engine: object,
        session_factory: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> Iterator[TestClient]:
        """An app whose browser-login wiring is on, so a cookie resolves.

        Without ``auth_oidc_login_enabled`` the container builds no session
        provider, so a cookie would fall through to the demo path and the guard
        would pass for the wrong reason — the cookie would be ignored, not
        honoured. This fixture makes the cookie real, which is the only version of
        the test that proves anything.
        """
        from notes_service.bootstrap import app as app_module
        from notes_service.bootstrap.container import build_container as real_build

        cookie_settings = settings.model_copy(
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

        def _builder(_settings: Settings) -> object:
            return real_build(_settings, engine=engine)  # type: ignore[arg-type]

        monkeypatch.setattr(app_module, "build_container", _builder)
        # HTTPS base URL so the client's own scheme matches the Secure cookie a
        # real browser session would hold, and so "same origin" means what it
        # means in production.
        with TestClient(
            app_module.create_app(cookie_settings),
            base_url="https://testserver",
            follow_redirects=False,
        ) as client:
            yield client

    def test_foreign_origin_cannot_create_a_note_with_a_session_cookie(
        self, cookie_client: TestClient, live_session: str
    ) -> None:
        response = cookie_client.post(
            "/api/v1/notes",
            json={"title": "forged", "content": "x"},
            headers={**cookie_header(live_session), "origin": "https://evil.example"},
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "csrf_rejected"

    def test_same_origin_cookie_request_reaches_the_application(
        self, cookie_client: TestClient, live_session: str
    ) -> None:
        # 201 or 422 both prove the guard let the request through; what must not
        # happen is a 403 from the guard. Assert on the absence of the refusal
        # rather than on a body shape owned by the notes use case.
        response = cookie_client.post(
            "/api/v1/notes",
            json={"title": "legitimate", "content": "x"},
            headers={**cookie_header(live_session), "origin": "https://testserver"},
        )
        assert response.status_code != 403, response.text
        assert response.status_code == 201, response.text

    def test_missing_origin_cookie_write_is_refused(
        self, cookie_client: TestClient, live_session: str
    ) -> None:
        response = cookie_client.post(
            "/api/v1/notes",
            json={"title": "no origin", "content": "x"},
            headers=cookie_header(live_session),
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["details"][0]["reason"] == "origin-missing"

    def test_bearer_caller_is_unaffected_by_the_guard(self, cookie_client: TestClient) -> None:
        response = cookie_client.post(
            "/api/v1/notes",
            json={"title": "bearer", "content": "x"},
            headers={"X-Demo-User-ID": str(ALICE)},
        )
        assert response.status_code == 201, response.text

    def test_safe_read_with_a_cookie_and_no_origin_is_served(
        self, cookie_client: TestClient, live_session: str
    ) -> None:
        response = cookie_client.get("/api/v1/notes", headers=cookie_header(live_session))
        assert response.status_code == 200, response.text

    def test_logout_from_a_foreign_origin_is_refused(
        self, cookie_client: TestClient, live_session: str
    ) -> None:
        # Logout reads the cookie without going through PrincipalDep, so only a
        # middleware-level guard covers it. A cross-site forced logout is a real,
        # if lesser, CSRF effect.
        response = cookie_client.post(
            "/api/v1/auth/logout",
            headers={**cookie_header(live_session), "origin": "https://evil.example"},
        )
        assert response.status_code == 403, response.text

    def test_refusal_does_not_echo_cors_permission(
        self, cookie_client: TestClient, live_session: str
    ) -> None:
        # The settings fixture allows http://localhost:3000 in CORS. A refusal
        # must not come back bearing an ``access-control-allow-origin`` header:
        # that would mean the CORS layer answered the forged request, and the
        # browser would be told the hostile origin may read the reply.
        response = cookie_client.post(
            "/api/v1/notes",
            json={"title": "forged", "content": "x"},
            headers={
                **cookie_header(live_session),
                "origin": "http://localhost:3000",
            },
        )
        assert response.status_code == 403, response.text
        assert "access-control-allow-origin" not in response.headers
