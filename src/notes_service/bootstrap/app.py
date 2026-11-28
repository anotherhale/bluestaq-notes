"""Application factory.

``create_app`` wires configuration, the composition root, middleware, routers,
and error handlers into a FastAPI instance. Tests build an app with their own
settings; production builds it from the environment.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from notes_service import __version__
from notes_service.bootstrap.config import Settings, load_settings
from notes_service.bootstrap.container import build_container
from notes_service.bootstrap.logging_config import configure_logging
from notes_service.presentation.api import api_router
from notes_service.presentation.csrf import CookieAuthOriginGuardMiddleware
from notes_service.presentation.errors import register_exception_handlers
from notes_service.presentation.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

logger = logging.getLogger("notes_service.app")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fully-wired FastAPI application."""
    settings = settings or load_settings()
    configure_logging(level=settings.log_level, as_json=settings.log_json)

    container = build_container(settings)

    app = FastAPI(
        title="Bluestaq Notes Service",
        version=__version__,
        description=_DESCRIPTION,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.container = container

    _add_middleware(app, settings)

    register_exception_handlers(app)
    app.include_router(api_router)
    _add_meta_routes(app)

    logger.info(
        "application configured",
        extra={"environment": settings.environment.value},
    )
    return app


def _add_middleware(app: FastAPI, settings: Settings) -> None:
    # Order matters: middleware added last runs first. We want the request
    # context (correlation id + logging) outermost so that every response,
    # including those produced by the body-size guard, carries a request id.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    if settings.security_headers_enabled:
        app.add_middleware(SecurityHeadersMiddleware)

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allow_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Demo-User-ID", "X-Request-ID"],
            max_age=600,
        )

    # Guard cookie-authenticated unsafe requests against a foreign origin. Added
    # after CORS so it runs *before* it (last added runs first): a forged write
    # must be refused by us, never answered by the CORS layer.
    app.add_middleware(
        CookieAuthOriginGuardMiddleware,
        cookie_name=settings.session_cookie_name,
        trusted_origins=settings.csrf_trusted_origins,
    )

    app.add_middleware(RequestContextMiddleware)


def _add_meta_routes(app: FastAPI) -> None:
    """Health and readiness endpoints (unauthenticated, unversioned)."""

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readiness", tags=["meta"], summary="Readiness probe")
    def readiness() -> dict[str, str]:
        # A deeper check (database round-trip) is intentionally omitted here to
        # keep the probe cheap; see docs/ARCHITECTURE.md for the rationale.
        return {"status": "ready"}


_DESCRIPTION = """\
A secure, multi-user notes REST service.

Notes are private by default. An owner may share a note with other users at
`read` or `write` level. Authentication in this deployment is provided by a
development identity mechanism (see the `X-Demo-User-ID` header) and is not
production authentication. See the repository README and `docs/` for details.
"""
