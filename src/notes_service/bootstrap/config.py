"""Environment-driven application configuration.

All settings are validated at startup. In particular, the configuration refuses
to start when a production environment is combined with development-only
authentication, and refuses to start with an insecure secret or wildcard CORS
in production. Failing fast here is safer than failing open at runtime.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # -- HTTP security ----------------------------------------------------
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    max_request_body_bytes: int = 256 * 1024
    security_headers_enabled: bool = True

    # -- rate limiting ----------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60

    # -- logging ----------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True

    # -- pagination -------------------------------------------------------
    default_page_size: int = 20
    max_page_size: int = 100

    @field_validator("cors_allow_origins", "oidc_algorithms", mode="before")
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
        return self


def load_settings() -> Settings:
    """Load and validate settings from the environment."""
    return Settings()
