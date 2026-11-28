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
from datetime import datetime, timedelta
from typing import TypeVar

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.ports.authorization import AuthorizationProvider
from notes_service.application.ports.decisioning import DecisionProvider
from notes_service.application.ports.identity import IdentityProvider
from notes_service.application.ports.operator_access import OperatorAccessLog
from notes_service.application.ports.operator_grants import OperatorGrantRepository
from notes_service.application.ports.organization_authorization import (
    OrganizationAuthorizationProvider,
)
from notes_service.application.ports.policy_versions import PolicyVersionRepository
from notes_service.application.ports.rate_limit import RateLimiter
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.application.ports.withholding import WithholdingRecorder
from notes_service.application.use_cases.agent_runs import (
    AbortAgentRun,
    AdvanceAgentRun,
    GetAgentRun,
    ListAgentRuns,
    PlanAgentRun,
    ResolveInterruptedStep,
)
from notes_service.application.use_cases.agents import (
    DecideApprovalRequest,
    GetAgent,
    GetApprovalRequest,
    GrantDelegation,
    IssueAgentToken,
    ListAgents,
    ListApprovalRequests,
    ListDelegations,
    ListToolInvocations,
    OpenApprovalRequest,
    RegisterAgent,
    ResolveAgentToken,
    RevokeAgent,
    RevokeApprovalRequest,
    RevokeDelegation,
    VerifyToolInvocationChain,
)
from notes_service.application.use_cases.approval_advisory import RecommendApprovalDecision
from notes_service.application.use_cases.audit_sweep import SweepAuditIntegrity
from notes_service.application.use_cases.authentication import (
    BeginLogin,
    CompleteLogin,
    EndSession,
    OidcUseCase,
    ResolveSession,
)
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.base import UseCase
from notes_service.application.use_cases.decisioning import EvaluateDecision
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
from notes_service.application.use_cases.identity_linking import (
    LinkExternalIdentity,
    ListUserExternalIdentities,
    UnlinkExternalIdentity,
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
from notes_service.application.use_cases.operator_access_report import GetOperatorAccessReport
from notes_service.application.use_cases.operator_access_retention import (
    PurgeOperatorAccessLog,
)
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.application.use_cases.operator_grants import (
    IssueOperatorGrant,
    ListOperatorGrants,
    ListRevokedOperatorGrants,
    RevokeOperatorGrant,
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
from notes_service.application.use_cases.policy_lifecycle import (
    ApproveDraft,
    DraftPolicy,
    GetPolicyHistory,
    ListPoliciesInForce,
    PublishDraft,
    RollbackPolicy,
)
from notes_service.application.use_cases.tools import InvokeTool
from notes_service.application.use_cases.withholding_report import GetWithholdingReport
from notes_service.bootstrap.config import Settings
from notes_service.domain.governance.retention import OperatorAccessRetentionPolicy
from notes_service.domain.identity.operator_grant import OperatorGrant
from notes_service.domain.identity.value_objects import UserId
from notes_service.infrastructure.db.engine import create_db_engine, create_session_factory
from notes_service.infrastructure.db.operator_access_log import (
    SqlAlchemyOperatorAccessLog,
)
from notes_service.infrastructure.db.operator_grants import (
    SqlAlchemyOperatorGrantRepository,
)
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.decisioning.http_provider import HttpDecisionProvider
from notes_service.infrastructure.decisioning.stub import StubDecisionProvider
from notes_service.infrastructure.governance.policy_versions_db import (
    SqlAlchemyPolicyVersionRepository,
)
from notes_service.infrastructure.identity.demo_provider import DemoIdentityProvider
from notes_service.infrastructure.identity.oidc_provider import OidcIdentityProvider
from notes_service.infrastructure.identity.oidc_relying_party import HttpOidcRelyingParty
from notes_service.infrastructure.identity.routing_provider import RoutingIdentityProvider
from notes_service.infrastructure.identity.session_provider import SessionIdentityProvider
from notes_service.infrastructure.security.clock import SystemClock
from notes_service.infrastructure.security.rate_limiter import InMemoryRateLimiter
from notes_service.infrastructure.security.withholding_recorder import (
    InMemoryWithholdingRecorder,
)

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
    # Counts context withholdings across advisories. Process-local today; the
    # port is what lets a shared store or exporter replace it without touching
    # the use case.
    withholding_recorder: WithholdingRecorder
    # Every granted read of a platform-wide operational view, in this process.
    # Exposed so a test (or a future ops endpoint) can answer "who read this"
    # without reaching into the gate.
    operator_access_log: OperatorAccessLog
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

    # External identity links. An operator holding ``MANAGE_MEMBERSHIPS``
    # attaches an IdP subject to a tenant member, which is what makes real
    # OIDC login resolve to a local account at all.
    link_external_identity: Callable[[], LinkExternalIdentity]
    unlink_external_identity: Callable[[], UnlinkExternalIdentity]
    list_user_external_identities: Callable[[], ListUserExternalIdentities]

    # Agent identity. Authority here is a chain — agent ceiling, then a user's
    # delegation, then a short-lived token — and every hop only narrows the one
    # above it. ``resolve_agent_token`` is the only entry point that turns a
    # presented secret back into an actor.
    register_agent: Callable[[], RegisterAgent]
    list_agents: Callable[[], ListAgents]
    get_agent: Callable[[], GetAgent]
    revoke_agent: Callable[[], RevokeAgent]
    grant_delegation: Callable[[], GrantDelegation]
    # Reading the grants is what makes *narrow* revocation usable: a delegation
    # id existed only in the POST that created it, so a lost response left
    # RevokeAgent (which also kills the agent) as the only reachable remedy.
    list_delegations: Callable[[], ListDelegations]
    revoke_delegation: Callable[[], RevokeDelegation]
    issue_agent_token: Callable[[], IssueAgentToken]
    resolve_agent_token: Callable[[], ResolveAgentToken]
    open_approval_request: Callable[[], OpenApprovalRequest]
    decide_approval_request: Callable[[], DecideApprovalRequest]
    revoke_approval_request: Callable[[], RevokeApprovalRequest]
    list_approval_requests: Callable[[], ListApprovalRequests]
    get_approval_request: Callable[[], GetApprovalRequest]
    # Reading the audit trail is the owning human's authority, matching approvals:
    # the trail names which tools an agent reached for, which is private to its
    # owner. Nothing here can write to the trail; only the gateway does that.
    list_tool_invocations: Callable[[], ListToolInvocations]
    # Recomputing the chain is the check that makes the trail evidence rather
    # than a log. Owner-only for the same reason as the listing.
    verify_tool_invocation_chain: Callable[[], VerifyToolInvocationChain]
    # The governed tool gateway. Every agent tool call goes through it, so it is
    # the one place capability checks, approval gating, and audit are applied.
    invoke_tool: Callable[[], InvokeTool]
    # Durable runs. Planning and advancing authenticate the agent by token; reading
    # and settling a run belong to the owning human, because only they can rule on
    # an interrupted step.
    plan_agent_run: Callable[[], PlanAgentRun]
    advance_agent_run: Callable[[], AdvanceAgentRun]
    get_agent_run: Callable[[], GetAgentRun]
    list_agent_runs: Callable[[], ListAgentRuns]
    resolve_interrupted_step: Callable[[], ResolveInterruptedStep]
    abort_agent_run: Callable[[], AbortAgentRun]

    # Judgment. ``evaluate_decision`` asks the configured provider one bounded
    # question. It is the *only* route to a decision, so the disabled flag, the
    # confidence threshold, and malformed-output policing apply to every caller.
    # Nothing it returns can grant authority.
    evaluate_decision: Callable[[], EvaluateDecision]

    # The first production caller of the judgment path. A human asks for a second
    # opinion about one pending approval; the answer is advisory and cannot
    # decide the request.
    recommend_approval_decision: Callable[[], RecommendApprovalDecision]
    # Process-wide operational totals, gated on ``ops_operator_user_ids``.
    get_withholding_report: Callable[[], GetWithholdingReport]
    # Platform-wide audit-chain verification, gated on ``ops_operator_user_ids``.
    sweep_audit_integrity: Callable[[], SweepAuditIntegrity]
    # The read side of the operator access record, gated the same way. Without
    # this the record is write-only and R36's question stays unanswerable.
    get_operator_access_report: Callable[[], GetOperatorAccessReport]
    # Who currently holds an operator grant, and until when (NEX-045). This is
    # the question a static allowlist could not answer: it held strings, not
    # holders.
    list_operator_grants: Callable[[], ListOperatorGrants]
    # Handing a grant to another user, bounded and attributable (NEX-074).
    issue_operator_grant: Callable[[], IssueOperatorGrant]
    # Taking a grant away, attributable and immediate (NEX-079).
    revoke_operator_grant: Callable[[], RevokeOperatorGrant]
    # Reading grants that were revoked, so the provenance NEX-079 records is
    # actually readable (NEX-080).
    list_revoked_operator_grants: Callable[[], ListRevokedOperatorGrants]
    # Pruning the access log by the configured retention window (NEX-075).
    # ``None`` when retention is disabled, so the endpoint can answer 404 rather
    # than expose a purge that would delete nothing.
    purge_operator_access_log: Callable[[], PurgeOperatorAccessLog | None]
    # The policy lifecycle (NEX-047/NEX-048). Durable: a published version is
    # what a past decision was made against, so it must survive a restart.
    draft_policy: Callable[[], DraftPolicy]
    approve_draft: Callable[[], ApproveDraft]
    publish_draft: Callable[[], PublishDraft]
    rollback_policy: Callable[[], RollbackPolicy]
    get_policy_history: Callable[[], GetPolicyHistory]
    list_policies_in_force: Callable[[], ListPoliciesInForce]


def _seed_operator_grants(
    grants: OperatorGrantRepository,
    configured: dict[UserId, frozenset[str]],
    *,
    now: datetime,
) -> None:
    """Turn the configured allowlist into stored, unbounded grants (NEX-045/046).

    ``OPS_OPERATOR_USER_IDS`` still describes the deployment's *initial* operator
    set, so nothing an operator already deployed relies on changes. What changes
    is what the gate reads: it consults the store, so a grant can carry an
    ``expires_at`` and an operator can enumerate holders. Configuration is now a
    seed path, not the allowlist.

    Seeded grants are unbounded and ``granted_by=None`` — no human issued them, and
    a deployment that wants a grant to lapse should issue it through the store
    rather than encode an expiry in an environment variable. Bounded grants exist
    precisely so they can be given out for an incident and then be gone.

    NEX-046 makes this *reconcile* rather than append. The store is durable now, so
    a seed that only appended would add the whole configuration again on every
    restart — the operator count would grow forever while nobody was granted
    anything new. ``replace_configured`` swaps out the configuration's own grants
    and leaves human-issued ones alone.
    """
    grants.replace_configured(
        OperatorGrant(user_id=user_id, scope=scope, granted_at=now, granted_by=None)
        for user_id, scopes in configured.items()
        for scope in sorted(scopes)
    )


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
    withholding_recorder = InMemoryWithholdingRecorder()
    # The record of *who read a platform-wide view* is durable for the same reason
    # the grants below are (R42, mirroring R40): an in-memory ring is per-worker and
    # cleared on restart, so the trail an operator audits described only the worker
    # that answered and vanished with a deploy. Append-only by construction.
    operator_access_log: OperatorAccessLog = SqlAlchemyOperatorAccessLog(session_factory)
    # Stored grants (NEX-045), durable since NEX-046. The environment variable is
    # a *seed path* into this store, not the allowlist itself: seeding at startup
    # produces one unbounded grant per configured scope, and the gate then
    # consults the store, so a grant can carry an expiry and an operator can
    # enumerate holders.
    #
    # It is a table rather than a dict because R40 was a real defect, not a
    # missing nicety: a process-local store forgot every bounded grant on restart
    # and each worker held its own copy, so a grant issued on one worker was
    # invisible to the others. It failed closed, so it was never a security hole —
    # but an operator could not audit a grant they could not see. ``replace_configured``
    # keeps the seed idempotent now that it survives a restart.
    operator_grants: OperatorGrantRepository = SqlAlchemyOperatorGrantRepository(session_factory)
    _seed_operator_grants(
        operator_grants,
        settings.operator_scopes,
        now=clock.now(),
    )

    # The stub is the default adapter: deterministic, offline, and it abstains
    # under any real threshold. A deployment opts into a real vendor by setting
    # DECISIONING_HTTP_ENDPOINT; that alone does not enable judgment, because
    # ``decisioning_enabled`` still gates every call. Selecting the provider here
    # rather than in each use case keeps one answer to "which provider is this
    # process using".
    def _build_decision_provider() -> DecisionProvider:
        if settings.decisioning_http_endpoint is None:
            return StubDecisionProvider()
        return HttpDecisionProvider(
            name=settings.decisioning_http_provider_name,
            endpoint=settings.decisioning_http_endpoint,
            api_key=settings.decisioning_http_api_key,
            timeout_seconds=settings.decisioning_http_timeout_seconds,
        )

    decision_provider: DecisionProvider = _build_decision_provider()

    def make_decision_evaluator() -> EvaluateDecision:
        return EvaluateDecision(
            provider=decision_provider,
            enabled=settings.decisioning_enabled,
            min_confidence=settings.decisioning_min_confidence,
        )

    def make_recommend_approval_decision() -> RecommendApprovalDecision:
        return RecommendApprovalDecision(
            uow_factory=uow_factory,
            authorization=authorization_provider,
            clock=clock,
            evaluate_decision=make_decision_evaluator(),
            withholding_recorder=withholding_recorder,
        )

    # One gate for every platform-wide operational view. Parsed here, inside
    # build_container, so the allowlist is converted once per process rather than
    # once per request. ``Settings`` has already validated every value is a
    # ``<user-id>:<scope>`` grant; an unset allowlist yields an empty map, which
    # the gate treats as "nobody". Sharing one gate is the point of R36: two
    # copies of this check can drift, and only one of them would have grown the
    # access record. Scoping is NEX-044: each grant names the operations it
    # covers, so reading one operational view never implies reading another.
    operator_gate = OperatorGate(
        grants=operator_grants,
        access_log=operator_access_log,
        clock=clock,
    )

    def make_get_withholding_report() -> GetWithholdingReport:
        return GetWithholdingReport(
            withholding_recorder=withholding_recorder,
            operator_gate=operator_gate,
        )

    def make_sweep_audit_integrity() -> SweepAuditIntegrity:
        return SweepAuditIntegrity(
            uow_factory=uow_factory,
            operator_gate=operator_gate,
        )

    # The record every granted operator read appends to is only useful if it can
    # be read. Same gate, so reading the trail is itself recorded and an operator
    # cannot inspect the log without adding to it.
    def make_get_operator_access_report() -> GetOperatorAccessReport:
        return GetOperatorAccessReport(
            access_log=operator_access_log,
            operator_gate=operator_gate,
        )

    # Who holds a grant right now, and until when. Same gate, so enumerating the
    # holders is itself a recorded operator read. The clock is injected because
    # the report states the instant it describes, and that instant must be the
    # same one the gate stamped the access entry with (R29).
    def make_list_operator_grants() -> ListOperatorGrants:
        return ListOperatorGrants(
            grants=operator_grants,
            operator_gate=operator_gate,
            clock=clock,
        )

    # The write side of the grant store. Bounded-only and attenuation-checked in
    # the use case, so this wiring only has to answer "does that user exist" — and
    # it answers from the same user repository every other use case reads.
    def make_issue_operator_grant() -> IssueOperatorGrant:
        def user_exists(user_id: UserId) -> bool:
            with uow_factory() as uow:
                return uow.users.get(user_id) is not None

        return IssueOperatorGrant(
            grants=operator_grants,
            operator_gate=operator_gate,
            clock=clock,
            user_exists=user_exists,
        )

    # The undo of the grant store (NEX-079). A revocation ends a grant immediately
    # and records who ended it, which is the only way to stop a mistaken hand-out
    # before its deadline — and the only way to stop a configuration grant at all.
    # It shares the issue use case's dependencies and nothing else: no user lookup,
    # because revoking from a user who does not exist is simply "nothing to revoke".
    def make_revoke_operator_grant() -> RevokeOperatorGrant:
        return RevokeOperatorGrant(
            grants=operator_grants,
            operator_gate=operator_gate,
            clock=clock,
        )

    # The read side of a revocation (NEX-080). NEX-079 wrote an attributable
    # revocation and nothing could read it back; this use case reads the same store
    # through the same read gate as the holder report, so an auditor who may see
    # who holds what may also see who lost it, and from whom.
    def make_list_revoked_operator_grants() -> ListRevokedOperatorGrants:
        return ListRevokedOperatorGrants(
            grants=operator_grants,
            operator_gate=operator_gate,
        )

    # Retention (NEX-075). Built only when the deployment configured a window:
    # ``None`` means "keep everything", and the endpoint then answers 404 rather
    # than offering a purge with no mandate. The policy validates the window when
    # it is constructed here, so a too-short or absurd value fails the boot, not
    # the first purge an operator runs.
    retention_policy: OperatorAccessRetentionPolicy | None = (
        None
        if settings.operator_access_retention_days is None
        else OperatorAccessRetentionPolicy(
            window=timedelta(days=settings.operator_access_retention_days)
        )
    )

    def make_purge_operator_access_log() -> PurgeOperatorAccessLog | None:
        if retention_policy is None:
            return None
        return PurgeOperatorAccessLog(
            access_log=operator_access_log,
            operator_gate=operator_gate,
            clock=clock,
            policy=retention_policy,
        )

    # Policy versions are stored, not held in memory: the published version in
    # force is evidence for decisions already taken, so it must outlive the
    # process that published it (NEX-048).
    policy_versions: PolicyVersionRepository = SqlAlchemyPolicyVersionRepository(session_factory)

    def make_draft_policy() -> DraftPolicy:
        return DraftPolicy(
            store=policy_versions,
            clock=clock,
            operator_gate=operator_gate,
            # Pass the real wiring, not an assumption: the authoring path refuses a
            # rule naming ``rate-limit`` exactly when the gateway would have no
            # limiter to enforce it with.
            rate_limiter_configured=rate_limiter is not None,
        )

    def make_approve_draft() -> ApproveDraft:
        return ApproveDraft(store=policy_versions, clock=clock, operator_gate=operator_gate)

    def make_publish_draft() -> PublishDraft:
        return PublishDraft(store=policy_versions, clock=clock, operator_gate=operator_gate)

    def make_rollback_policy() -> RollbackPolicy:
        return RollbackPolicy(store=policy_versions, clock=clock, operator_gate=operator_gate)

    def make_get_policy_history() -> GetPolicyHistory:
        return GetPolicyHistory(store=policy_versions, operator_gate=operator_gate)

    # The published set, reported as data. ``rate_limiter_configured`` is the same
    # value the gateway and the drafting guard receive, so the enforcement gaps
    # this report names are the gaps the gateway would refuse on.
    def make_list_policies_in_force() -> ListPoliciesInForce:
        return ListPoliciesInForce(
            store=policy_versions,
            operator_gate=operator_gate,
            clock=clock,
            rate_limiter_configured=rate_limiter is not None,
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

    def make_tool_gateway() -> InvokeTool:
        """Build the gateway, which additionally needs the token resolver.

        A fresh resolver is built with each gateway, so token, delegation, and
        agent revocation are re-checked on every invocation rather than cached.
        """
        return InvokeTool(
            uow_factory=uow_factory,
            authorization=authorization_provider,
            clock=clock,
            resolve_token=ResolveAgentToken(
                uow_factory=uow_factory,
                authorization=authorization_provider,
                clock=clock,
            ),
            # Published policy is enforced here, at the one seam every agent
            # action passes through (NEX-051). Until this was wired the lifecycle
            # could publish rules nothing consulted.
            policy_versions=policy_versions,
            # A policy's ``rate-limit`` obligation is enforced against this limiter
            # (NEX-056). It is the same port the HTTP boundary uses, so swapping in
            # a shared store covers both. Omitting it would not silently disable the
            # obligation: the gateway reports ``rate-limit`` as unsatisfiable and
            # refuses the call.
            rate_limiter=rate_limiter,
        )

    def make_plan_agent_run() -> PlanAgentRun:
        return PlanAgentRun(
            uow_factory=uow_factory,
            authorization=authorization_provider,
            clock=clock,
            resolve_token=ResolveAgentToken(
                uow_factory=uow_factory,
                authorization=authorization_provider,
                clock=clock,
            ),
        )

    def make_advance_agent_run() -> AdvanceAgentRun:
        """A run advances *through* the gateway, never around it.

        The run owns ordering and durability; the gateway still owns capability,
        approval, and audit. Composing them here is what makes "a run cannot reach
        a tool its token lacks" true by construction rather than by convention.
        """
        return AdvanceAgentRun(
            uow_factory=uow_factory,
            authorization=authorization_provider,
            clock=clock,
            resolve_token=ResolveAgentToken(
                uow_factory=uow_factory,
                authorization=authorization_provider,
                clock=clock,
            ),
            gateway=make_tool_gateway(),
        )

    return Container(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        identity_provider=identity_provider,
        authorization_provider=authorization_provider,
        rate_limiter=rate_limiter,
        withholding_recorder=withholding_recorder,
        operator_access_log=operator_access_log,
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
        link_external_identity=make_organization(LinkExternalIdentity),
        unlink_external_identity=make_organization(UnlinkExternalIdentity),
        list_user_external_identities=make_organization(ListUserExternalIdentities),
        register_agent=make(RegisterAgent),
        list_agents=make(ListAgents),
        get_agent=make(GetAgent),
        revoke_agent=make(RevokeAgent),
        grant_delegation=make(GrantDelegation),
        list_delegations=make(ListDelegations),
        revoke_delegation=make(RevokeDelegation),
        issue_agent_token=make(IssueAgentToken),
        resolve_agent_token=make(ResolveAgentToken),
        open_approval_request=make(OpenApprovalRequest),
        decide_approval_request=make(DecideApprovalRequest),
        revoke_approval_request=make(RevokeApprovalRequest),
        list_approval_requests=make(ListApprovalRequests),
        get_approval_request=make(GetApprovalRequest),
        list_tool_invocations=make(ListToolInvocations),
        verify_tool_invocation_chain=make(VerifyToolInvocationChain),
        invoke_tool=make_tool_gateway,
        plan_agent_run=make_plan_agent_run,
        advance_agent_run=make_advance_agent_run,
        get_agent_run=make(GetAgentRun),
        list_agent_runs=make(ListAgentRuns),
        resolve_interrupted_step=make(ResolveInterruptedStep),
        abort_agent_run=make(AbortAgentRun),
        evaluate_decision=make_decision_evaluator,
        recommend_approval_decision=make_recommend_approval_decision,
        get_withholding_report=make_get_withholding_report,
        sweep_audit_integrity=make_sweep_audit_integrity,
        get_operator_access_report=make_get_operator_access_report,
        list_operator_grants=make_list_operator_grants,
        issue_operator_grant=make_issue_operator_grant,
        revoke_operator_grant=make_revoke_operator_grant,
        list_revoked_operator_grants=make_list_revoked_operator_grants,
        purge_operator_access_log=make_purge_operator_access_log,
        draft_policy=make_draft_policy,
        approve_draft=make_approve_draft,
        publish_draft=make_publish_draft,
        rollback_policy=make_rollback_policy,
        get_policy_history=make_get_policy_history,
        list_policies_in_force=make_list_policies_in_force,
    )
