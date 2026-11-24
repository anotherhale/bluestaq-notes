"""Browser login endpoints: authorization-code + PKCE.

Three routes under ``/api/v1/auth``:

``GET /auth/login``
    Mints a single-use login transaction and redirects the browser to the IdP.
``GET /auth/callback``
    Receives the IdP redirect, exchanges the code, and sets the session cookie.
``POST /auth/logout``
    Revokes the caller's session and clears the cookie.

Every route returns ``404`` when browser login is not enabled, so a deployment
without an IdP does not advertise a login surface. The security rules live in
the use cases; these handlers only translate between HTTP and plain data.

The callback is unauthenticated by necessity (it *is* the authentication step),
so it is rate limited by client address. It never echoes the authorization code
or the IdP's error body, and it never reports *why* a login failed beyond a
generic message.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse

from notes_service.application.errors import AuthenticationError, InvalidInput, ResourceNotFound
from notes_service.bootstrap.container import Container
from notes_service.presentation.dependencies import (
    AnonymousRateLimitDep,
    ContainerDep,
)

router = APIRouter(prefix="/auth", tags=["authentication"])

#: Where the browser lands after a completed login. Relative, so it is resolved
#: against the same origin the callback was served from.
_POST_LOGIN_REDIRECT = "/"


def _require_login_enabled(container: Container) -> None:
    """Fail closed with ``404`` when browser login is not configured."""
    if (
        not container.settings.auth_oidc_login_enabled
        or container.relying_party is None
        or container.begin_login is None
        or container.complete_login is None
        or container.end_session is None
    ):
        raise ResourceNotFound("not found")


def _set_session_cookie(response: Response, container: Container, token: str) -> None:
    settings = container.settings
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_absolute_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


@router.get(
    "/login",
    summary="Begin browser login (redirects to the identity provider)",
    dependencies=[AnonymousRateLimitDep],
    status_code=307,
)
def begin_login(
    container: ContainerDep,
    login_hint: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    _require_login_enabled(container)
    begin = container.begin_login
    if begin is None:  # pragma: no cover - narrowed by _require_login_enabled
        raise ResourceNotFound("not found")

    request = begin().run(login_hint=login_hint)
    # The transaction is persisted before this redirect, so a callback carrying
    # an unknown state can never be accepted.
    return RedirectResponse(url=request.authorization_url, status_code=307)


@router.get(
    "/callback",
    summary="Complete browser login (identity provider redirect target)",
    dependencies=[AnonymousRateLimitDep],
    status_code=303,
)
def complete_login(
    container: ContainerDep,
    state: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    _require_login_enabled(container)
    if error:
        # The IdP declined the request; never surface its detail.
        raise AuthenticationError("login was not completed")
    if not state or not code:
        raise InvalidInput("missing authorization response parameters")

    complete = container.complete_login
    if complete is None:  # pragma: no cover - narrowed by _require_login_enabled
        raise ResourceNotFound("not found")

    established = complete().run(state=state, code=code)

    response = RedirectResponse(url=_POST_LOGIN_REDIRECT, status_code=303)
    _set_session_cookie(response, container, established.session_token)
    return response


@router.post(
    "/logout",
    summary="Revoke the current session and clear the cookie",
    dependencies=[AnonymousRateLimitDep],
    status_code=204,
)
def logout(container: ContainerDep, request: Request) -> Response:
    _require_login_enabled(container)
    settings = container.settings
    token = request.cookies.get(settings.session_cookie_name)

    end_session = container.end_session
    if token and end_session is not None:
        # Revocation is idempotent and silent for an unknown token, so logout
        # never reveals whether a session existed.
        end_session().run(session_token=token)

    response = Response(status_code=204)
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )
    return response


__all__ = ["router"]
