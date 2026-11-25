"""FastAPI dependencies.

These functions adapt framework objects (``Request``, ``app.state``) into the
transport-neutral inputs the use cases expect, and enforce authentication and
rate limiting uniformly. Business logic never lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Query, Request

from notes_service.application.errors import AuthenticationError, InvalidInput, RateLimited
from notes_service.application.ports.identity import IdentityProvider
from notes_service.bootstrap.container import Container
from notes_service.domain.agent.token import hash_agent_token
from notes_service.domain.identity.principal import Principal
from notes_service.infrastructure.identity.context import HeaderAuthenticationContext


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


ContainerDep = Annotated[Container, Depends(get_container)]


def get_principal(request: Request, container: ContainerDep) -> Principal:
    """Authenticate the request into a :class:`Principal`.

    Authentication happens exactly once per request. The resolved principal id is
    stashed on ``request.state`` for the access log; it is never trusted from the
    request body.
    """
    provider: IdentityProvider = container.identity_provider
    context = HeaderAuthenticationContext.from_headers(
        dict(request.headers), cookie_name=container.settings.session_cookie_name
    )
    principal = provider.authenticate(context)
    request.state.user_id = str(principal.user_id)
    request.state.auth_provider = principal.provider
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def enforce_rate_limit(principal: PrincipalDep, container: ContainerDep) -> None:
    """Apply the configured rate limit to every protected API request.

    Depending on ``PrincipalDep`` guarantees authentication runs first, so the
    limit is keyed by the authenticated principal id rather than a shared client
    address. One user's traffic therefore cannot exhaust another user's budget.
    Unauthenticated requests never reach this point; they are rejected with 401
    by authentication, which is the deny-by-default behaviour we want.
    """
    settings = container.settings
    if not settings.rate_limit_enabled:
        return

    decision = container.rate_limiter.check(f"user:{principal.user_id}")
    if not decision.allowed:
        raise RateLimited(
            "rate limit exceeded; please retry later", retry_after=decision.retry_after
        )


RateLimitDep = Depends(enforce_rate_limit)


def get_agent_token_secret(request: Request) -> str:
    """Extract the agent token an agent presents to the tool gateway.

    The gateway's caller *is* the agent, so the token is the credential and
    there is no human principal to authenticate. Only the ``Authorization``
    header is consulted: the demo-identity header is deliberately ignored here,
    so a demo user cannot reach the gateway on a human's behalf, and a missing
    or malformed header fails closed with 401 rather than an anonymous call.
    """
    context = HeaderAuthenticationContext.from_headers(dict(request.headers))
    header = context.authorization_header
    if not header:
        raise AuthenticationError("an agent token is required")
    scheme, _, secret = header.partition(" ")
    if scheme.lower() != "bearer" or not secret.strip():
        raise AuthenticationError("an agent token must be supplied as a bearer credential")
    return secret.strip()


AgentTokenSecretDep = Annotated[str, Depends(get_agent_token_secret)]


def enforce_agent_rate_limit(secret: AgentTokenSecretDep, container: ContainerDep) -> None:
    """Rate limit the tool gateway per agent token, not per client address.

    Keying on a digest of the presented token keeps one agent's traffic from
    exhausting another's budget, and keeps the raw secret out of limiter keys
    and any log line derived from them.
    """
    settings = container.settings
    if not settings.rate_limit_enabled:
        return
    digest = hash_agent_token(secret)
    decision = container.rate_limiter.check(f"agent-token:{digest[:32]}")
    if not decision.allowed:
        raise RateLimited(
            "rate limit exceeded; please retry later", retry_after=decision.retry_after
        )


AgentRateLimitDep = Depends(enforce_agent_rate_limit)


def enforce_anonymous_rate_limit(request: Request, container: ContainerDep) -> None:
    """Rate limit an unauthenticated endpoint, keyed by client address.

    The demo-identity discovery endpoint cannot key on a principal (it exists to
    bootstrap authentication), so it keys on the client address instead. This is
    best-effort: behind a proxy the address is the proxy's unless a trusted
    ``X-Forwarded-For`` is configured, which is acceptable for a development
    affordance.
    """
    settings = container.settings
    if not settings.rate_limit_enabled:
        return
    client = request.client.host if request.client else "unknown"
    decision = container.rate_limiter.check(f"anon:{client}")
    if not decision.allowed:
        raise RateLimited(
            "rate limit exceeded; please retry later", retry_after=decision.retry_after
        )


AnonymousRateLimitDep = Depends(enforce_anonymous_rate_limit)


def require_authentication(principal: PrincipalDep) -> Principal:
    """Dependency that guarantees a principal is present (fails closed)."""
    if principal is None:  # pragma: no cover - get_principal already raises
        raise AuthenticationError("authentication required")
    return principal


@dataclass(frozen=True, slots=True)
class Pagination:
    """Validated pagination parameters shared by list endpoints.

    Bounds come from configuration so ``DEFAULT_PAGE_SIZE``/``MAX_PAGE_SIZE``
    are honoured consistently across every list endpoint.
    """

    limit: int
    offset: int


def get_pagination(
    container: ContainerDep,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    settings = container.settings
    effective = settings.default_page_size if limit is None else limit
    if effective > settings.max_page_size:
        raise InvalidInput(f"limit must be at most {settings.max_page_size}")
    return Pagination(limit=effective, offset=offset)


PaginationDep = Annotated[Pagination, Depends(get_pagination)]
