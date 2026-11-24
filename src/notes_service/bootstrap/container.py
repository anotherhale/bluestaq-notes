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
from datetime import timedelta
from typing import TypeVar

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.ports.authorization import AuthorizationProvider
from notes_service.application.ports.identity import IdentityProvider
from notes_service.application.ports.organization_authorization import (
    OrganizationAuthorizationProvider,
)
from notes_service.application.ports.rate_limit import RateLimiter
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.application.use_cases.agents import (
    DecideApprovalRequest,
    GetAgent,
    GrantDelegation,
    IssueAgentToken,
    ListAgents,
    ListApprovalRequests,
    OpenApprovalRequest,
    RegisterAgent,
    ResolveAgentToken,
    RevokeAgent,
    RevokeDelegation,
)
from notes_service.application.use_cases.authentication import (
    BeginLogin,
    CompleteLogin,
    EndSession,
    OidcUseCase,
    ResolveSession,
)
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.base import UseCase
from notes_service.application.use_cases.folders import (
    CreateFolder,
    DeleteFolder,
    ListFolders,
    RenameFolder,
)
from notes_service.application.use_cases.identity import (
    GetCurrentUser,
    ListDemoIdentities,
    ListUsers,
)
from notes_service.application.use_cases.notes import (
    CreateNote,
    DeleteNote,
    GetNote,
    ListNotes,
    MoveNote,
    StarNote,
    UnstarNote,
    UpdateNote,
)
from notes_service.application.use_cases.organization import (
    AddTeamMember,
    ChangeMemberRole,
    CreateOrganization,
    CreateOrganizationUnit,
    CreateTeam,
    DeprovisionMember,
    GetOrganization,
    InviteOrganizationMember,
    ListMyOrganizations,
    ListOrganizationMembers,
    ListOrganizationUnits,
    ListTeamMembers,
    ListTeams,
    MoveOrganizationUnit,
    OrganizationUseCase,
    ReactivateMember,
    RemoveTeamMember,
    SetMemberManager,
    SuspendMember,
    UpdateOrganization,
)
from notes_service.application.use_cases.organization_policy import (
    DefaultOrganizationAuthorizationProvider,
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
from notes_service.infrastructure.identity.oidc_relying_party import HttpOidcRelyingParty
from notes_service.infrastructure.identity.routing_provider import RoutingIdentityProvider
from notes_service.infrastructure.identity.session_provider import SessionIdentityProvider
from notes_service.infrastructure.security.clock import SystemClock
from notes_service.infrastructure.security.rate_limiter import InMemoryRateLimiter

UseCaseT = TypeVar("UseCaseT", bound=UseCase)
LoginUseCaseT = TypeVar("LoginUseCaseT", bound=OidcUseCase)
# Organization use cases need the second policy port, so they are bound to their
# own base class. This is what makes mypy reject a factory that forgets to pass
# ``organization_authorization`` — the omission that silently broke every org
# endpoint at runtime while the generic ``**kwargs`` signature hid it.
OrganizationUseCaseT = TypeVar("OrganizationUseCaseT", bound=OrganizationUseCase)


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

    # Browser login (authorization-code + PKCE). These are ``None`` unless
    # ``auth_oidc_login_enabled`` is set, and the login router returns 404 when
    # they are absent — a deployment without browser login must not expose a
    # half-wired login endpoint.
    relying_party: HttpOidcRelyingParty | None
    begin_login: Callable[[], BeginLogin] | None
    complete_login: Callable[[], CompleteLogin] | None
    end_session: Callable[[], EndSession] | None

    # Use-case factories: call with no arguments to obtain a fresh use case,
    # each backed by its own Unit of Work. Typed so callers get the concrete
    # use-case class rather than a bare ``partial``.
    get_current_user: Callable[[], GetCurrentUser]
    list_users: Callable[[], ListUsers]
    list_demo_identities: Callable[[], ListDemoIdentities]
    create_note: Callable[[], CreateNote]
    get_note: Callable[[], GetNote]
    list_notes: Callable[[], ListNotes]
    update_note: Callable[[], UpdateNote]
    delete_note: Callable[[], DeleteNote]
    move_note: Callable[[], MoveNote]
    star_note: Callable[[], StarNote]
    unstar_note: Callable[[], UnstarNote]
    create_folder: Callable[[], CreateFolder]
    list_folders: Callable[[], ListFolders]
    rename_folder: Callable[[], RenameFolder]
    delete_folder: Callable[[], DeleteFolder]
    share_note: Callable[[], ShareNote]
    revoke_permission: Callable[[], RevokePermission]
    list_permissions: Callable[[], ListPermissions]

    # Organization administration. These are the only entry points the admin
    # console uses; each enforces its own capability check in the application
    # layer, so the router never decides authorization.
    create_organization: Callable[[], CreateOrganization]
    list_my_organizations: Callable[[], ListMyOrganizations]
    get_organization: Callable[[], GetOrganization]
    update_organization: Callable[[], UpdateOrganization]
    create_organization_unit: Callable[[], CreateOrganizationUnit]
    list_organization_units: Callable[[], ListOrganizationUnits]
    move_organization_unit: Callable[[], MoveOrganizationUnit]
    create_team: Callable[[], CreateTeam]
    list_teams: Callable[[], ListTeams]
    invite_organization_member: Callable[[], InviteOrganizationMember]
    list_organization_members: Callable[[], ListOrganizationMembers]
    change_member_role: Callable[[], ChangeMemberRole]
    set_member_manager: Callable[[], SetMemberManager]
    suspend_member: Callable[[], SuspendMember]
    reactivate_member: Callable[[], ReactivateMember]
    deprovision_member: Callable[[], DeprovisionMember]
    add_team_member: Callable[[], AddTeamMember]
    remove_team_member: Callable[[], RemoveTeamMember]
    list_team_members: Callable[[], ListTeamMembers]

    # Agent identity. Authority here is a chain — agent ceiling, then a user's
    # delegation, then a short-lived token — and every hop only narrows the one
    # above it. ``resolve_agent_token`` is the only entry point that turns a
    # presented secret back into an actor.
    register_agent: Callable[[], RegisterAgent]
    list_agents: Callable[[], ListAgents]
    get_agent: Callable[[], GetAgent]
    revoke_agent: Callable[[], RevokeAgent]
    grant_delegation: Callable[[], GrantDelegation]
    revoke_delegation: Callable[[], RevokeDelegation]
    issue_agent_token: Callable[[], IssueAgentToken]
    resolve_agent_token: Callable[[], ResolveAgentToken]
    open_approval_request: Callable[[], OpenApprovalRequest]
    decide_approval_request: Callable[[], DecideApprovalRequest]
    list_approval_requests: Callable[[], ListApprovalRequests]


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

    clock = SystemClock()

    # Browser login wiring. The relying party and the login use cases exist only
    # when explicitly enabled; otherwise the router 404s, so a deployment that
    # never configured an IdP cannot accidentally expose a login endpoint.
    relying_party: HttpOidcRelyingParty | None = None
    begin_login: Callable[[], BeginLogin] | None = None
    complete_login: Callable[[], CompleteLogin] | None = None
    end_session: Callable[[], EndSession] | None = None
    session_provider: IdentityProvider | None = None

    if settings.auth_oidc_login_enabled:
        relying_party = HttpOidcRelyingParty(
            provider_name=settings.oidc_provider_name,
            issuer=settings.oidc_issuer or "",
            client_id=settings.oidc_client_id or "",
            client_secret=settings.oidc_client_secret,
            authorization_endpoint=settings.oidc_authorization_endpoint or "",
            token_endpoint=settings.oidc_token_endpoint or "",
            jwks_url=settings.oidc_jwks_url or "",
            redirect_uri=settings.oidc_redirect_uri or "",
            scopes=tuple(settings.oidc_scopes),
            algorithms=tuple(settings.oidc_algorithms),
            leeway_seconds=settings.oidc_leeway_seconds,
        )
        idle_ttl = timedelta(seconds=settings.session_idle_ttl_seconds)
        absolute_ttl = timedelta(seconds=settings.session_absolute_ttl_seconds)

        def _login_use_case(use_case_type: type[LoginUseCaseT]) -> Callable[[], LoginUseCaseT]:
            def factory() -> LoginUseCaseT:
                return use_case_type(
                    uow_factory=uow_factory,
                    clock=clock,
                    relying_party=relying_party,
                    session_idle_ttl=idle_ttl,
                    session_absolute_ttl=absolute_ttl,
                )

            return factory

        begin_login = _login_use_case(BeginLogin)
        complete_login = _login_use_case(CompleteLogin)
        end_session = _login_use_case(EndSession)
        resolve_session = _login_use_case(ResolveSession)

        session_provider = SessionIdentityProvider(
            cookie_name=settings.session_cookie_name,
            resolve_session=resolve_session,
            context_token=lambda context: context.session_cookie,
        )

    identity_provider = RoutingIdentityProvider(
        oidc_provider=oidc_provider,
        demo_provider=demo_provider,
        session_provider=session_provider,
    )
    authorization_provider = DefaultAuthorizationProvider()
    # Administration authority is a separate policy from content access: an org
    # admin must never gain the ability to read members' notes.
    organization_authorization_provider: OrganizationAuthorizationProvider = (
        DefaultOrganizationAuthorizationProvider()
    )

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

    def make_organization(
        use_case_type: type[OrganizationUseCaseT],
    ) -> Callable[[], OrganizationUseCaseT]:
        """Build an organization use case, which needs the second policy port."""

        def factory() -> OrganizationUseCaseT:
            return use_case_type(
                uow_factory=uow_factory,
                authorization=authorization_provider,
                organization_authorization=organization_authorization_provider,
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
        relying_party=relying_party,
        begin_login=begin_login,
        complete_login=complete_login,
        end_session=end_session,
        get_current_user=make(GetCurrentUser),
        list_users=make(ListUsers),
        list_demo_identities=make(ListDemoIdentities),
        create_note=make(CreateNote),
        get_note=make(GetNote),
        list_notes=make(ListNotes),
        update_note=make(UpdateNote),
        delete_note=make(DeleteNote),
        move_note=make(MoveNote),
        star_note=make(StarNote),
        unstar_note=make(UnstarNote),
        create_folder=make(CreateFolder),
        list_folders=make(ListFolders),
        rename_folder=make(RenameFolder),
        delete_folder=make(DeleteFolder),
        share_note=make(ShareNote),
        revoke_permission=make(RevokePermission),
        list_permissions=make(ListPermissions),
        create_organization=make_organization(CreateOrganization),
        list_my_organizations=make_organization(ListMyOrganizations),
        get_organization=make_organization(GetOrganization),
        update_organization=make_organization(UpdateOrganization),
        create_organization_unit=make_organization(CreateOrganizationUnit),
        list_organization_units=make_organization(ListOrganizationUnits),
        move_organization_unit=make_organization(MoveOrganizationUnit),
        create_team=make_organization(CreateTeam),
        list_teams=make_organization(ListTeams),
        invite_organization_member=make_organization(InviteOrganizationMember),
        list_organization_members=make_organization(ListOrganizationMembers),
        change_member_role=make_organization(ChangeMemberRole),
        set_member_manager=make_organization(SetMemberManager),
        suspend_member=make_organization(SuspendMember),
        reactivate_member=make_organization(ReactivateMember),
        deprovision_member=make_organization(DeprovisionMember),
        add_team_member=make_organization(AddTeamMember),
        remove_team_member=make_organization(RemoveTeamMember),
        list_team_members=make_organization(ListTeamMembers),
        register_agent=make(RegisterAgent),
        list_agents=make(ListAgents),
        get_agent=make(GetAgent),
        revoke_agent=make(RevokeAgent),
        grant_delegation=make(GrantDelegation),
        revoke_delegation=make(RevokeDelegation),
        issue_agent_token=make(IssueAgentToken),
        resolve_agent_token=make(ResolveAgentToken),
        open_approval_request=make(OpenApprovalRequest),
        decide_approval_request=make(DecideApprovalRequest),
        list_approval_requests=make(ListApprovalRequests),
    )
