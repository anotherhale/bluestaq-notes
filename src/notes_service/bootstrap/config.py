"""Environment-driven application configuration.

All settings are validated at startup. In particular, the configuration refuses
to start when a production environment is combined with development-only
authentication, and refuses to start with an insecure secret or wildcard CORS
in production. Failing fast here is safer than failing open at runtime.
"""

from __future__ import annotations

import json
import uuid
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from notes_service.domain.identity.value_objects import UserId

OPERATOR_WILDCARD = "*"
"""Scope token meaning "every operational view" (see ``OperatorGate``)."""


def _parse_operator_grant(entry: str) -> tuple[UserId, str]:
    """Split one ``<user-id>:<scope>`` grant, rejecting anything malformed.

    Kept a module function rather than a method so the field validator and the
    ``operator_scopes`` property cannot disagree about what a valid grant is: they
    call the same parser, so a value that validates is a value that parses.
    """
    text = entry.strip()
    user_text, separator, scope = text.partition(":")
    if not separator:
        raise ValueError(
            f"ops_operator_user_ids entry {entry!r} must be '<user-id>:<scope>' "
            f"(use {OPERATOR_WILDCARD!r} to grant every operation)"
        )
    scope = scope.strip()
    if not scope:
        raise ValueError(f"ops_operator_user_ids entry {entry!r} names no scope")
    # Parse with ``uuid`` rather than ``UserId.parse``: the domain raises a
    # ``DomainError`` for a malformed id, which pydantic would not wrap into a
    # startup ``ValidationError``, so the operator would see a domain traceback
    # instead of the offending configuration entry.
    try:
        user_id = UserId(str(uuid.UUID(user_text.strip())))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"ops_operator_user_ids entry {entry!r} has a non-UUID user id") from exc
    return user_id, scope


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"

    @property
    def is_production(self) -> bool:
        return self is Environment.PRODUCTION


class DatabaseProvider(StrEnum):
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


class Settings(BaseSettings):
    """Typed application settings loaded from the environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- environment ------------------------------------------------------
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False

    # -- database ---------------------------------------------------------
    database_provider: DatabaseProvider = DatabaseProvider.SQLITE
    database_url: str = "sqlite:///./notes.db"

    # -- authentication ---------------------------------------------------
    # When true, the development X-Demo-User-ID header is honoured. Forced off
    # in production by the validator below.
    auth_demo_enabled: bool = True

    # OIDC (optional; required only when auth_oidc_enabled is true).
    auth_oidc_enabled: bool = False
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_algorithms: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["RS256"])
    oidc_leeway_seconds: int = 30

    # Browser login (authorization-code + PKCE). Separate from the bearer-token
    # settings above because a deployment may use either or both.
    auth_oidc_login_enabled: bool = False
    oidc_provider_name: str = "oidc"
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_authorization_endpoint: str | None = None
    oidc_token_endpoint: str | None = None
    oidc_redirect_uri: str | None = None
    oidc_scopes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["openid", "profile", "email"]
    )

    # Session cookie. ``Secure`` is forced on in production by the validator
    # below, because a session cookie sent over plain HTTP is replayable.
    session_cookie_name: str = "notes_session"
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_idle_ttl_seconds: int = 8 * 60 * 60
    session_absolute_ttl_seconds: int = 7 * 24 * 60 * 60

    # -- HTTP security ----------------------------------------------------
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    max_request_body_bytes: int = 256 * 1024
    security_headers_enabled: bool = True

    # -- rate limiting ----------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60

    # -- decisioning ------------------------------------------------------
    # Off by default. Judgment is an enhancement, never a precondition: with the
    # flag off every evaluation abstains and deterministic behaviour applies.
    decisioning_enabled: bool = False
    # Confidence a provider must reach before its opinion is usable at all.
    decisioning_min_confidence: float = 0.5
    # Optional external judgment service. When BOTH are unset the offline stub is
    # used, which abstains under any real threshold. Setting the endpoint is what
    # opts a deployment into a real vendor; it does not by itself enable
    # decisioning, because ``decisioning_enabled`` still gates every call.
    decisioning_http_endpoint: str | None = None
    decisioning_http_api_key: str | None = None
    # Recorded in telemetry and returned to clients, so it must be an identifier.
    # A deployment running two vendors (e.g. a canary) distinguishes them here
    # rather than by parsing the endpoint host.
    decisioning_http_provider_name: str = "http"
    decisioning_http_timeout_seconds: float = 5.0

    # -- operations -------------------------------------------------------
    # Who may read which process-wide operational view, as ``<user-id>:<scope>``
    # entries. EMPTY BY DEFAULT, and empty means *nobody*: every operational view
    # spans all tenants, so it must be granted explicitly rather than inherited by
    # being logged in. Anyone not granted the named operation gets 404, not 403.
    #
    # A scope is the operation a use case names (``withholding-report``,
    # ``audit-integrity``, ``operator-access-report``) or ``*`` for every
    # operation. A scope that is not named is not granted, so reading one
    # operational view never implies reading another.
    #
    # Examples: ``ops_operator_user_ids=1111...:withholding-report,2222...:*``
    ops_operator_user_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # -- logging ----------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True

    # -- pagination -------------------------------------------------------
    default_page_size: int = 20
    max_page_size: int = 100

    @field_validator(
        "cors_allow_origins",
        "oidc_algorithms",
        "oidc_scopes",
        "ops_operator_user_ids",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept comma-separated values or a JSON array from the environment.

        ``NoDecode`` disables pydantic-settings' automatic JSON decoding for
        these fields so the friendly comma-separated form documented in
        ``.env.example`` works; a JSON array is still accepted for convenience.
        """
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError("expected a comma-separated list or JSON array") from exc
                return parsed
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @field_validator("ops_operator_user_ids")
    @classmethod
    def _validate_operator_ids(cls, value: list[str]) -> list[str]:
        """Reject a malformed grant at startup rather than at request time.

        The gate compares ``UserId`` values and exact operation names, so a typo
        in either half would otherwise match nobody and the endpoint would look
        broken for the one operator it was meant to serve. Failing here names the
        offending value in the startup error instead.

        A bare id with no ``:`` is rejected rather than read as "all operations".
        Silently widening an unscoped legacy entry into a wildcard grant is the
        escalation this field exists to prevent.
        """
        for entry in value:
            _parse_operator_grant(entry)
        return value

    @property
    def operator_scopes(self) -> dict[UserId, frozenset[str]]:
        """The allowlist as ``user id -> permitted operations``.

        Built from the validated entries. Several entries for one user union
        their scopes, so a deployment can grant one person two views without
        repeating every operation on one line.
        """
        scopes: dict[UserId, set[str]] = {}
        for entry in self.ops_operator_user_ids:
            user_id, scope = _parse_operator_grant(entry)
            scopes.setdefault(user_id, set()).add(scope)
        return {user_id: frozenset(ops) for user_id, ops in scopes.items()}

    @field_validator("decisioning_http_endpoint")
    @classmethod
    def _validate_decisioning_endpoint(cls, value: str | None) -> str | None:
        """Require HTTPS at startup.

        The adapter enforces this too, but the adapter is constructed lazily per
        use case. A deployment that misconfigured the scheme should learn at boot,
        not on the first advisory a human is waiting for.
        """
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if not text.lower().startswith("https://"):
            raise ValueError("decisioning_http_endpoint must be an https:// URL")
        return text

    @field_validator("decisioning_http_timeout_seconds")
    @classmethod
    def _validate_decisioning_timeout(cls, value: float) -> float:
        if not 0 < value <= 60:
            raise ValueError("decisioning_http_timeout_seconds must be in (0, 60]")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return upper

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value or "://" not in value:
            raise ValueError("database_url must be a valid SQLAlchemy URL")
        return value

    @field_validator("max_request_body_bytes")
    @classmethod
    def _validate_body_limit(cls, value: int) -> int:
        if value < 1024:
            raise ValueError("max_request_body_bytes must be at least 1024")
        return value

    @model_validator(mode="after")
    def _enforce_security_invariants(self) -> Settings:
        # Provider/URL consistency: catch a mismatched config early.
        scheme = self.database_url.split("://", 1)[0]
        if self.database_provider is DatabaseProvider.SQLITE and not scheme.startswith("sqlite"):
            raise ValueError("database_provider=sqlite requires a sqlite:// database_url")
        if self.database_provider is DatabaseProvider.POSTGRESQL and not scheme.startswith(
            "postgresql"
        ):
            raise ValueError(
                "database_provider=postgresql requires a postgresql+...:// database_url"
            )

        if self.auth_oidc_enabled and not (self.oidc_issuer and self.oidc_audience):
            raise ValueError("auth_oidc_enabled requires oidc_issuer and oidc_audience")

        if self.auth_oidc_login_enabled:
            required = {
                "oidc_issuer": self.oidc_issuer,
                "oidc_client_id": self.oidc_client_id,
                "oidc_authorization_endpoint": self.oidc_authorization_endpoint,
                "oidc_token_endpoint": self.oidc_token_endpoint,
                "oidc_jwks_url": self.oidc_jwks_url,
                "oidc_redirect_uri": self.oidc_redirect_uri,
            }
            missing = sorted(name for name, value in required.items() if not value)
            if missing:
                raise ValueError(f"auth_oidc_login_enabled requires {', '.join(missing)}")

        if self.session_cookie_samesite == "none" and not self.session_cookie_secure:
            raise ValueError("SameSite=None requires a Secure session cookie")
        if self.session_idle_ttl_seconds <= 0 or self.session_absolute_ttl_seconds <= 0:
            raise ValueError("session TTLs must be positive")
        if self.session_idle_ttl_seconds > self.session_absolute_ttl_seconds:
            raise ValueError("session_idle_ttl_seconds must not exceed the absolute TTL")

        if self.environment.is_production:
            if self.auth_demo_enabled:
                raise ValueError(
                    "auth_demo_enabled must be false in production: demo authentication is "
                    "development-only"
                )
            if self.debug:
                raise ValueError("debug must be false in production")
            if not self.cors_allow_origins:
                raise ValueError(
                    "cors_allow_origins must list explicit origins in production "
                    "(wildcard is not permitted)"
                )
            if "*" in self.cors_allow_origins:
                raise ValueError("wildcard CORS origins are not permitted in production")
            if not self.session_cookie_secure:
                raise ValueError(
                    "session_cookie_secure must be true in production: a session cookie "
                    "sent over plain HTTP is replayable"
                )
        return self


def load_settings() -> Settings:
    """Load and validate settings from the environment."""
    return Settings()
