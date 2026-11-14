"""HTTP middleware: request correlation, body-size limits, security headers,
and access logging.

Middleware runs outermost-first in the order added. The composition root adds
these in a deliberate order so that even a 413 (body too large) or a 500 carries
a correlation id and security headers.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TypeGuard

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger("notes_service.http")

REQUEST_ID_HEADER = "X-Request-ID"

# Conservative, broadly safe defaults. ``X-Frame-Options`` and a restrictive CSP
# are appropriate for a JSON API; HSTS is only meaningful over HTTPS but is
# harmless to send and is commonly expected.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a correlation id and emits a structured access log line."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if _is_valid_request_id(incoming) else str(uuid.uuid4())
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "request failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        user_id = getattr(request.state, "user_id", None)
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "user_id": user_id,
            },
        )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects request bodies larger than the configured limit.

    The check uses ``Content-Length`` when present, which is cheap and covers
    the common case. It is a guard rail, not a substitute for a proxy-level
    limit; a chunked body without ``Content-Length`` is bounded by the server.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                return _error_response(
                    request, 400, "invalid_content_length", "invalid Content-Length header"
                )
            if length > self._max_bytes:
                return _error_response(
                    request,
                    413,
                    "payload_too_large",
                    f"request body exceeds the {self._max_bytes} byte limit",
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds baseline security headers to every response."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = {"error": {"code": code, "message": message}, "request_id": request_id}
    response = JSONResponse(status_code=status_code, content=body)
    if request_id:
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


def _is_valid_request_id(value: str | None) -> TypeGuard[str]:
    if not value:
        return False
    # Accept only bounded, printable, header-safe values with no whitespace or
    # control characters, to avoid log injection and header smuggling.
    if len(value) > 128:
        return False
    return all(33 <= ord(char) <= 126 for char in value)
