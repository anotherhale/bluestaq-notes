"""Authentication tests: demo identity, rejection, and production safety."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from notes_service.application.errors import AuthenticationError
from notes_service.bootstrap.config import Environment, Settings
from notes_service.infrastructure.identity.context import HeaderAuthenticationContext
from notes_service.infrastructure.identity.demo_provider import DemoIdentityProvider
from notes_service.infrastructure.identity.routing_provider import RoutingIdentityProvider
from tests.conftest import ALICE, DAVE, auth_headers


class TestDemoIdentityOverHttp:
    def test_resolves_known_demo_user(self, client: TestClient) -> None:
        response = client.get("/api/v1/me", headers=auth_headers(ALICE))
        assert response.status_code == 200
        assert response.json()["email"] == "alice@example.com"

    def test_missing_identity_is_401(self, client: TestClient) -> None:
        response = client.get("/api/v1/me")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    def test_unknown_demo_user_rejected(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/me", headers={"X-Demo-User-ID": "99999999-9999-9999-9999-999999999999"}
        )
        assert response.status_code == 401

    def test_malformed_demo_user_rejected(self, client: TestClient) -> None:
        response = client.get("/api/v1/me", headers={"X-Demo-User-ID": "not-a-uuid"})
        assert response.status_code == 401

    def test_suspended_user_rejected(self, client: TestClient) -> None:
        # Dave is seeded with SUSPENDED status.
        response = client.get("/api/v1/me", headers=auth_headers(DAVE))
        assert response.status_code == 401

    def test_protected_endpoints_require_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/notes").status_code == 401
        assert client.post("/api/v1/notes", json={"title": "t", "content": ""}).status_code == 401
        assert client.get("/api/v1/users").status_code == 401


class TestRoutingProviderNoDowngrade:
    """A failed bearer token must never fall back to demo authentication."""

    def test_invalid_bearer_does_not_fall_back_to_demo(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/me",
            headers={"Authorization": "Bearer not.a.real.token", "X-Demo-User-ID": ALICE},
        )
        # No OIDC provider is enabled, so a bearer token is rejected outright
        # rather than being downgraded to the demo identity in the same request.
        assert response.status_code == 401

    def test_bearer_without_oidc_enabled_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/v1/me", headers={"Authorization": "Bearer abc"})
        assert response.status_code == 401

    def test_demo_header_without_bearer_still_works(self, client: TestClient) -> None:
        response = client.get("/api/v1/me", headers=auth_headers(ALICE))
        assert response.status_code == 200


class TestDemoProviderUnit:
    def test_disabled_provider_rejects(self, session_factory) -> None:  # type: ignore[no-untyped-def]
        provider = DemoIdentityProvider(
            uow_factory=lambda: None, enabled=False, environment_is_production=False
        )
        with pytest.raises(AuthenticationError):
            provider.authenticate(HeaderAuthenticationContext(demo_user_id_header=ALICE))

    def test_production_construction_forbidden(self) -> None:
        with pytest.raises(ValueError):
            DemoIdentityProvider(
                uow_factory=lambda: None, enabled=True, environment_is_production=True
            )

    def test_routing_provider_requires_at_least_one(self) -> None:
        with pytest.raises(ValueError):
            RoutingIdentityProvider(oidc_provider=None, demo_provider=None)


class TestProductionConfigurationRejectsDemo:
    def test_settings_reject_demo_in_production(self) -> None:
        with pytest.raises(ValueError, match="demo"):
            Settings(
                environment=Environment.PRODUCTION,
                auth_demo_enabled=True,
                cors_allow_origins=["https://app.example.com"],
                database_url="sqlite:///./prod.db",
            )

    def test_settings_reject_wildcard_cors_in_production(self) -> None:
        with pytest.raises(ValueError, match="wildcard"):
            Settings(
                environment=Environment.PRODUCTION,
                auth_demo_enabled=False,
                auth_oidc_enabled=True,
                oidc_issuer="https://issuer.example.com",
                oidc_audience="notes",
                cors_allow_origins=["*"],
                database_url="sqlite:///./prod.db",
            )

    def test_settings_accept_secure_production_config(self) -> None:
        settings = Settings(
            environment=Environment.PRODUCTION,
            auth_demo_enabled=False,
            auth_oidc_enabled=True,
            oidc_issuer="https://issuer.example.com",
            oidc_audience="notes",
            oidc_jwks_url="https://issuer.example.com/.well-known/jwks.json",
            cors_allow_origins=["https://app.example.com"],
            database_url="postgresql+psycopg://notes:notes@localhost:5432/notes",
            database_provider="postgresql",
        )
        assert settings.environment is Environment.PRODUCTION
        assert settings.auth_demo_enabled is False

    def test_app_refuses_to_start_with_demo_in_production(self) -> None:
        # The settings validator is the first line of defence.
        with pytest.raises(ValueError):
            Settings(
                environment=Environment.PRODUCTION,
                auth_demo_enabled=True,
                cors_allow_origins=["https://app.example.com"],
                database_url="sqlite:///./prod.db",
            )


class TestListSettingsFromEnvironment:
    """List-valued settings must accept the comma-separated form from .env."""

    def test_cors_origins_accepts_comma_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://a.example.com, https://b.example.com")
        settings = Settings(database_url="sqlite://")
        assert settings.cors_allow_origins == [
            "https://a.example.com",
            "https://b.example.com",
        ]

    def test_cors_origins_accepts_json_array(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", '["https://a.example.com"]')
        settings = Settings(database_url="sqlite://")
        assert settings.cors_allow_origins == ["https://a.example.com"]

    def test_oidc_algorithms_accepts_comma_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OIDC_ALGORITHMS", "RS256,ES256")
        settings = Settings(database_url="sqlite://")
        assert settings.oidc_algorithms == ["RS256", "ES256"]


class TestSessionCookieContext:
    """The ``Cookie`` header must be parsed without help from the framework."""

    def test_extracts_named_cookie(self) -> None:
        context = HeaderAuthenticationContext.from_headers(
            {"Cookie": "other=1; notes_session=abc123"}
        )
        assert context.session_cookie == "abc123"

    def test_absent_cookie_header_yields_none(self) -> None:
        assert HeaderAuthenticationContext.from_headers({}).session_cookie is None

    def test_cookie_name_is_configurable(self) -> None:
        context = HeaderAuthenticationContext.from_headers(
            {"Cookie": "notes_session=abc; my_sess=xyz"}, cookie_name="my_sess"
        )
        assert context.session_cookie == "xyz"

    def test_unrelated_cookie_does_not_match(self) -> None:
        context = HeaderAuthenticationContext.from_headers({"Cookie": "notes_session_x=abc"})
        assert context.session_cookie is None

    def test_empty_value_is_treated_as_absent(self) -> None:
        # ``notes_session=`` must not authenticate as an empty token.
        context = HeaderAuthenticationContext.from_headers({"Cookie": "notes_session="})
        assert context.session_cookie is None


class TestSessionCookieIsAuthoritative:
    """A presented session cookie must never be downgraded to the demo header.

    This is the security-critical half of the routing rule: if a stale or forged
    cookie fell through to demo authentication, an attacker could pin a broken
    cookie and silently assume whatever demo identity they named in a header.
    """

    def test_inert_cookie_does_not_break_demo_auth(self, client: TestClient) -> None:
        # With browser login disabled there is no session provider, so a stray
        # cookie is simply ignored and the demo header still governs. This
        # asserts the cookie is *inert*, not that it authenticates anyone: the
        # demo identity here is one the caller already named in a header.
        response = client.get(
            "/api/v1/me",
            headers={"Cookie": "notes_session=forged", "X-Demo-User-ID": ALICE},
        )
        assert response.status_code == 200
        assert response.json()["email"] == "alice@example.com"

    def test_cookie_alone_never_authenticates_without_login_enabled(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/v1/me", headers={"Cookie": "notes_session=forged"})
        assert response.status_code == 401

    def test_routing_prefers_session_over_demo(self) -> None:
        from notes_service.domain.identity.principal import Principal
        from notes_service.domain.identity.value_objects import UserId

        class _RejectingSession:
            name = "session"

            def authenticate(self, context: object) -> Principal:
                raise AuthenticationError("bad cookie")

        class _RecordingDemo:
            name = "demo"
            called = False

            def authenticate(self, context: object) -> Principal:
                self.called = True
                return Principal(
                    user_id=UserId("11111111-1111-1111-1111-111111111111"),
                    provider="demo",
                    subject="demo",
                )

        demo = _RecordingDemo()
        provider = RoutingIdentityProvider(
            oidc_provider=None, demo_provider=demo, session_provider=_RejectingSession()
        )
        context = HeaderAuthenticationContext(
            demo_user_id_header="11111111-1111-1111-1111-111111111111",
            session_cookie="forged",
        )

        with pytest.raises(AuthenticationError):
            provider.authenticate(context)
        assert demo.called is False, "demo provider must not run when a cookie was presented"
