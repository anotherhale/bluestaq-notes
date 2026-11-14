"""Translation of application/domain errors into HTTP responses.

All error responses share one envelope::

    {"error": {"code": ..., "message": ..., "details": [...]}, "request_id": ...}

Mapping policy (see docs/SECURITY.md and ADR-008):

* AuthenticationError -> 401
* AccessDenied         -> 403 (caller already knows the resource exists)
* ResourceNotFound     -> 404 (also used to hide inaccessible resources)
* Conflict            -> 409
* InvalidInput        -> 422
* RateLimited         -> 429
* Domain ValidationError / Pydantic validation -> 422
* Anything unhandled  -> 500 (details never leaked to the client)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from notes_service.application.errors import (
    AccessDenied,
    AuthenticationError,
    Conflict,
    InvalidInput,
    RateLimited,
    ResourceNotFound,
)
from notes_service.domain.errors import InvariantViolation, ValidationError, VersionConflict
from notes_service.presentation.schemas import ErrorBody, ErrorResponse

logger = logging.getLogger("notes_service.errors")

# Starlette renamed the 422 constant; prefer the new name when available so we
# do not emit a deprecation warning, and fall back for older versions.
HTTP_422_UNPROCESSABLE = (
    getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", None) or status.HTTP_422_UNPROCESSABLE_ENTITY
)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _json_error(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, object]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details),
        request_id=_request_id(request),
    )
    return JSONResponse(
        status_code=status_code, content=body.model_dump(mode="json"), headers=headers
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the centralized exception handlers to the application."""

    @app.exception_handler(AuthenticationError)
    async def _auth_error(request: Request, exc: AuthenticationError) -> JSONResponse:
        return _json_error(
            request=request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthenticated",
            message=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(AccessDenied)
    async def _access_denied(request: Request, exc: AccessDenied) -> JSONResponse:
        return _json_error(
            request=request,
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            message=str(exc),
        )

    @app.exception_handler(ResourceNotFound)
    async def _not_found(request: Request, exc: ResourceNotFound) -> JSONResponse:
        return _json_error(
            request=request,
            status_code=status.HTTP_404_NOT_FOUND,
            code="not_found",
            message=str(exc),
        )

    @app.exception_handler(Conflict)
    async def _conflict(request: Request, exc: Conflict) -> JSONResponse:
        return _json_error(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code="conflict",
            message=str(exc),
        )

    @app.exception_handler(InvalidInput)
    async def _invalid_input(request: Request, exc: InvalidInput) -> JSONResponse:
        return _json_error(
            request=request,
            status_code=HTTP_422_UNPROCESSABLE,
            code="invalid_input",
            message=str(exc),
        )

    @app.exception_handler(RateLimited)
    async def _rate_limited(request: Request, exc: RateLimited) -> JSONResponse:
        return _json_error(
            request=request,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="rate_limited",
            message=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(VersionConflict)
    async def _version_conflict(request: Request, exc: VersionConflict) -> JSONResponse:
        return _json_error(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code="conflict",
            message=str(exc),
        )

    @app.exception_handler(InvariantViolation)
    async def _invariant(request: Request, exc: InvariantViolation) -> JSONResponse:
        # An invariant breach that reached this point indicates the request was
        # rejected by a business rule; treat it as a conflict/forbidden class of
        # client error rather than a server fault.
        return _json_error(
            request=request,
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            message="operation not permitted",
        )

    @app.exception_handler(ValidationError)
    async def _domain_validation(request: Request, exc: ValidationError) -> JSONResponse:
        return _json_error(
            request=request,
            status_code=HTTP_422_UNPROCESSABLE,
            code="validation_error",
            message=str(exc),
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details: list[dict[str, object]] = []
        for err in exc.errors():
            details.append(
                {
                    "loc": [str(part) for part in err.get("loc", [])],
                    "msg": err.get("msg", ""),
                    "type": err.get("type", ""),
                }
            )
        return _json_error(
            request=request,
            status_code=HTTP_422_UNPROCESSABLE,
            code="validation_error",
            message="request validation failed",
            details=details,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log with traceback but never surface internals to the client.
        logger.exception(
            "unhandled error",
            extra={"request_id": _request_id(request), "path": request.url.path},
        )
        return _json_error(
            request=request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="an unexpected error occurred",
        )
