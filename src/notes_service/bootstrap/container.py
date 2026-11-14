"""The composition root.

Every concrete dependency is constructed here, once, and wired into the
application. This is the only module that knows about both the application ports
and their infrastructure adapters. There is no service locator and no global
mutable state: the container is created by the application factory and stored on
``app.state``.

Use cases are built lazily as ``functools.partial`` factories so a fresh Unit of
Work is created per request while sharing the process-wide engine, providers,
and rate limiter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.ports.authorization import AuthorizationProvider
from notes_service.application.ports.identity import IdentityProvider
from notes_service.application.ports.rate_limit import RateLimiter
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.base import UseCase
from notes_service.application.use_cases.identity import GetCurrentUser, ListUsers
from notes_service.application.use_cases.notes import (
    CreateNote,
    DeleteNote,
    GetNote,
    ListNotes,
    UpdateNote,
)
from notes_service.application.use_cases.permissions import (
    ListPermissions,
    RevokePermission,
    ShareNote,
)
from notes_service.bootstrap.config import Settings
from notes_service.infrastructure.db.engine import create_db_engine, create_session_factory
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.identity.demo_provider import DemoIdentityProvider
from notes_service.infrastructure.identity.oidc_provider import OidcIdentityProvider
from notes_service.infrastructure.identity.routing_provider import RoutingIdentityProvider
from notes_service.infrastructure.security.clock import SystemClock
from notes_service.infrastructure.security.rate_limiter import InMemoryRateLimiter

UseCaseT = TypeVar("UseCaseT", bound=UseCase)


@dataclass
class Container:
    """Holds the wired singletons for one application instance."""

    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    identity_provider: IdentityProvider
    authorization_provider: AuthorizationProvider
    rate_limiter: RateLimiter
    clock: SystemClock

    # Use-case factories: call with no arguments to obtain a fresh use case,
    # each backed by its own Unit of Work. Typed so callers get the concrete
    # use-case class rather than a bare ``partial``.
    get_current_user: Callable[[], GetCurrentUser]
    list_users: Callable[[], ListUsers]
    create_note: Callable[[], CreateNote]
    get_note: Callable[[], GetNote]
    list_notes: Callable[[], ListNotes]
    update_note: Callable[[], UpdateNote]
    delete_note: Callable[[], DeleteNote]
    share_note: Callable[[], ShareNote]
    revoke_permission: Callable[[], RevokePermission]
    list_permissions: Callable[[], ListPermissions]


def build_container(settings: Settings, engine: Engine | None = None) -> Container:
    """Construct the full dependency graph from validated settings.

    ``engine`` may be supplied by tests to target an existing database; in
    normal operation it is built from the settings.
    """
    if engine is None:
        engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    demo_provider: IdentityProvider | None = None
    if settings.auth_demo_enabled:
        demo_provider = DemoIdentityProvider(
            uow_factory=uow_factory,
            enabled=True,
            environment_is_production=settings.environment.is_production,
        )

    oidc_provider: IdentityProvider | None = None
    if settings.auth_oidc_enabled:
        oidc_provider = OidcIdentityProvider(
            uow_factory=uow_factory,
            issuer=settings.oidc_issuer or "",
            audience=settings.oidc_audience or "",
            jwks_url=settings.oidc_jwks_url or "",
            algorithms=list(settings.oidc_algorithms),
            leeway_seconds=settings.oidc_leeway_seconds,
        )

    identity_provider = RoutingIdentityProvider(
        oidc_provider=oidc_provider, demo_provider=demo_provider
    )
    authorization_provider = DefaultAuthorizationProvider()
    clock = SystemClock()

    rate_limiter = InMemoryRateLimiter(
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )

    def make(use_case_type: type[UseCaseT]) -> Callable[[], UseCaseT]:
        def factory() -> UseCaseT:
            return use_case_type(
                uow_factory=uow_factory,
                authorization=authorization_provider,
                clock=clock,
            )

        return factory

    return Container(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        identity_provider=identity_provider,
        authorization_provider=authorization_provider,
        rate_limiter=rate_limiter,
        clock=clock,
        get_current_user=make(GetCurrentUser),
        list_users=make(ListUsers),
        create_note=make(CreateNote),
        get_note=make(GetNote),
        list_notes=make(ListNotes),
        update_note=make(UpdateNote),
        delete_note=make(DeleteNote),
        share_note=make(ShareNote),
        revoke_permission=make(RevokePermission),
        list_permissions=make(ListPermissions),
    )
