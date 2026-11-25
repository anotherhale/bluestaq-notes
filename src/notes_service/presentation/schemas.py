"""Pydantic v2 request and response schemas.

These schemas are the public contract. They are intentionally separate from both
the domain entities and the ORM rows so that neither leaks across the HTTP
boundary. ``model_config`` uses ``extra="forbid"`` on request bodies so that
unexpected fields are rejected rather than silently ignored.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from notes_service.application.ports.withholding import WithholdingSnapshot
from notes_service.application.use_cases.agent_runs import RunAdvance
from notes_service.application.use_cases.agents import IssuedToken, ResolvedToken
from notes_service.application.use_cases.approval_advisory import ApprovalAdvisory
from notes_service.application.use_cases.organization import OrganizationView
from notes_service.application.use_cases.tools import ToolResult
from notes_service.domain.agent.audit_chain import ChainVerdict
from notes_service.domain.agent.decisioning import AbstentionReason
from notes_service.domain.agent.delegation import DelegatedAuthorization
from notes_service.domain.agent.entities import AgentIdentity, ApprovalRequest
from notes_service.domain.agent.runs import AgentRun, RunStatus, RunStep, StepStatus
from notes_service.domain.agent.tools import InvocationStatus, ToolInvocation
from notes_service.domain.agent.value_objects import (
    AgentStatus,
    ApprovalStatus,
    Capability,
    DelegationStatus,
    TokenAudience,
)
from notes_service.domain.identity.entities import User
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.notes.folder import Folder
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.permission import NotePermission
from notes_service.domain.notes.value_objects import (
    MAX_CONTENT_BYTES,
    MAX_FOLDER_NAME_LENGTH,
    MAX_TITLE_LENGTH,
    PermissionLevel,
)
from notes_service.domain.organization.entities import (
    OrganizationMembership,
    OrganizationUnit,
    Team,
    TeamMembership,
)
from notes_service.domain.organization.value_objects import (
    MAX_NAME_LENGTH,
    MAX_SLUG_LENGTH,
    MembershipStatus,
    OrganizationRole,
    OrganizationStatus,
    OrgPermission,
    OrgUnitType,
    TeamRole,
    TeamStatus,
    UnitStatus,
)

MAX_ORGANIZATION_NAME_LENGTH = MAX_NAME_LENGTH

# Optional free-text field (a person's job title). Not a domain value object, so
# the bound lives here purely to reject pathological payloads.
MAX_TITLE_FIELD_LENGTH = 120


class StrictModel(BaseModel):
    """Base for request models: reject unknown fields."""

    model_config = ConfigDict(extra="forbid")


class ResponseModel(BaseModel):
    """Base for response models: serialise from domain objects."""

    model_config = ConfigDict(from_attributes=True)


# -- identity -------------------------------------------------------------


class UserResponse(ResponseModel):
    """A user as exposed for sharing. Only sharing-relevant fields are returned."""

    id: uuid.UUID
    email: str
    display_name: str

    @classmethod
    def from_domain(cls, user: User) -> UserResponse:
        return cls(
            id=uuid.UUID(str(user.id)), email=str(user.email), display_name=user.display_name
        )


class CurrentUserResponse(ResponseModel):
    """The authenticated caller. Includes status, which is safe for self."""

    id: uuid.UUID
    email: str
    display_name: str
    status: str

    @classmethod
    def from_domain(cls, user: User) -> CurrentUserResponse:
        return cls(
            id=uuid.UUID(str(user.id)),
            email=str(user.email),
            display_name=user.display_name,
            status=user.status.value,
        )


# -- shared ---------------------------------------------------------------


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


# -- notes ----------------------------------------------------------------


class NoteCreateRequest(StrictModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    content: str = Field(default="", max_length=MAX_CONTENT_BYTES)
    # Optional placement at creation; omitted means the owner's root.
    folder_id: uuid.UUID | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class NoteUpdateRequest(StrictModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    content: str = Field(default="", max_length=MAX_CONTENT_BYTES)
    # Optimistic concurrency: the client must state the version it edited.
    version: int = Field(ge=1)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class NoteMoveRequest(StrictModel):
    """Move a note into a folder (``folder_id``) or to the root (``null``)."""

    folder_id: uuid.UUID | None = None


class NoteResponse(ResponseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    content: str
    version: int
    folder_id: uuid.UUID | None
    is_starred: bool
    # The caller's *own* effective level. Exposing only the caller's level (not
    # the full collaborator list) is what lets a collaborator know they can edit
    # without leaking who else the note is shared with.
    caller_permission: PermissionLevel | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, note: Note, *, caller_id: uuid.UUID) -> NoteResponse:
        return cls(
            id=uuid.UUID(str(note.id)),
            owner_id=uuid.UUID(str(note.owner_id)),
            title=str(note.title),
            content=str(note.content),
            version=int(note.version),
            folder_id=uuid.UUID(str(note.folder_id)) if note.folder_id is not None else None,
            is_starred=note.is_starred,
            caller_permission=note.effective_level(UserId(str(caller_id))),
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


class NoteSummaryResponse(ResponseModel):
    """A note without its content, for list endpoints."""

    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    version: int
    folder_id: uuid.UUID | None
    is_starred: bool
    caller_permission: PermissionLevel | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, note: Note, *, caller_id: uuid.UUID) -> NoteSummaryResponse:
        return cls(
            id=uuid.UUID(str(note.id)),
            owner_id=uuid.UUID(str(note.owner_id)),
            title=str(note.title),
            version=int(note.version),
            folder_id=uuid.UUID(str(note.folder_id)) if note.folder_id is not None else None,
            is_starred=note.is_starred,
            caller_permission=note.effective_level(UserId(str(caller_id))),
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


# -- folders --------------------------------------------------------------


class FolderCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=MAX_FOLDER_NAME_LENGTH)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


class FolderRenameRequest(StrictModel):
    name: str = Field(min_length=1, max_length=MAX_FOLDER_NAME_LENGTH)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


class FolderResponse(ResponseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, folder: Folder) -> FolderResponse:
        return cls(
            id=uuid.UUID(str(folder.id)),
            owner_id=uuid.UUID(str(folder.owner_id)),
            name=str(folder.name),
            created_at=folder.created_at,
            updated_at=folder.updated_at,
        )


class FolderListResponse(BaseModel):
    items: list[FolderResponse]
    meta: PageMeta


# -- permissions ----------------------------------------------------------


class PermissionGrantRequest(StrictModel):
    user_id: uuid.UUID
    permission: PermissionLevel

    @field_validator("permission", mode="before")
    @classmethod
    def _normalise_permission(cls, value: object) -> object:
        if isinstance(value, str):
            return value.lower()
        return value


class PermissionResponse(ResponseModel):
    user_id: uuid.UUID
    permission: PermissionLevel
    granted_by: uuid.UUID
    created_at: datetime

    @classmethod
    def from_domain(cls, permission: NotePermission) -> PermissionResponse:
        return cls(
            user_id=uuid.UUID(str(permission.user_id)),
            permission=permission.level,
            granted_by=uuid.UUID(str(permission.granted_by)),
            created_at=permission.created_at,
        )


# -- organizations ---------------------------------------------------------


class OrganizationCreateRequest(StrictModel):
    """Create an organization. The caller becomes its owner."""

    name: str = Field(min_length=1, max_length=MAX_ORGANIZATION_NAME_LENGTH)
    slug: str = Field(min_length=1, max_length=MAX_SLUG_LENGTH)
    title: str | None = Field(default=None, max_length=MAX_TITLE_FIELD_LENGTH)


class OrganizationUpdateRequest(StrictModel):
    """Rename and/or change status. ``version`` enables optimistic concurrency."""

    name: str | None = Field(default=None, min_length=1, max_length=MAX_ORGANIZATION_NAME_LENGTH)
    status: OrganizationStatus | None = None
    version: int | None = Field(default=None, ge=1)


class OrganizationResponse(ResponseModel):
    id: uuid.UUID
    slug: str
    name: str
    status: OrganizationStatus
    version: int
    created_at: datetime
    updated_at: datetime
    # The caller's own role and capabilities in this organization. Only the
    # caller's own view is exposed, never the full membership list.
    caller_role: OrganizationRole
    caller_capabilities: list[OrgPermission]

    @classmethod
    def from_view(cls, view: OrganizationView) -> OrganizationResponse:
        organization = view.organization
        return cls(
            id=uuid.UUID(str(organization.id)),
            slug=str(organization.slug),
            name=str(organization.name),
            status=organization.status,
            version=organization.version,
            created_at=organization.created_at,
            updated_at=organization.updated_at,
            caller_role=view.membership.role,
            caller_capabilities=sorted(view.capabilities),
        )


class OrganizationListResponse(BaseModel):
    items: list[OrganizationResponse]
    meta: PageMeta


class OrganizationUnitCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=MAX_ORGANIZATION_NAME_LENGTH)
    slug: str = Field(min_length=1, max_length=MAX_SLUG_LENGTH)
    unit_type: OrgUnitType = OrgUnitType.DEPARTMENT
    parent_id: uuid.UUID | None = None


class OrganizationUnitMoveRequest(StrictModel):
    """Re-parent a unit. ``null`` promotes it to the organization root."""

    parent_id: uuid.UUID | None = None


class OrganizationUnitResponse(ResponseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    slug: str
    unit_type: OrgUnitType
    parent_id: uuid.UUID | None
    status: UnitStatus
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, unit: OrganizationUnit) -> OrganizationUnitResponse:
        return cls(
            id=uuid.UUID(str(unit.id)),
            organization_id=uuid.UUID(str(unit.organization_id)),
            name=str(unit.name),
            slug=str(unit.slug),
            unit_type=unit.unit_type,
            parent_id=uuid.UUID(str(unit.parent_id)) if unit.parent_id else None,
            status=unit.status,
            version=unit.version,
            created_at=unit.created_at,
            updated_at=unit.updated_at,
        )


class OrganizationUnitListResponse(BaseModel):
    items: list[OrganizationUnitResponse]
    meta: PageMeta


class TeamCreateRequest(StrictModel):
    unit_id: uuid.UUID
    name: str = Field(min_length=1, max_length=MAX_ORGANIZATION_NAME_LENGTH)
    slug: str = Field(min_length=1, max_length=MAX_SLUG_LENGTH)
    parent_team_id: uuid.UUID | None = None


class TeamResponse(ResponseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    unit_id: uuid.UUID
    name: str
    slug: str
    parent_team_id: uuid.UUID | None
    status: TeamStatus
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, team: Team) -> TeamResponse:
        return cls(
            id=uuid.UUID(str(team.id)),
            organization_id=uuid.UUID(str(team.organization_id)),
            unit_id=uuid.UUID(str(team.unit_id)),
            name=str(team.name),
            slug=str(team.slug),
            parent_team_id=uuid.UUID(str(team.parent_team_id)) if team.parent_team_id else None,
            status=team.status,
            version=team.version,
            created_at=team.created_at,
            updated_at=team.updated_at,
        )


class TeamListResponse(BaseModel):
    items: list[TeamResponse]
    meta: PageMeta


class MemberInviteRequest(StrictModel):
    """Invite an existing user into the organization with an organization role."""

    user_id: uuid.UUID
    role: OrganizationRole = OrganizationRole.ORG_MEMBER
    manager_user_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=MAX_TITLE_FIELD_LENGTH)


class MemberRoleChangeRequest(StrictModel):
    role: OrganizationRole


class MemberManagerRequest(StrictModel):
    """Set or clear the reporting line. ``null`` removes the manager."""

    manager_user_id: uuid.UUID | None = None


class MembershipResponse(ResponseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    role: OrganizationRole
    status: MembershipStatus
    manager_user_id: uuid.UUID | None
    title: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, membership: OrganizationMembership) -> MembershipResponse:
        return cls(
            id=uuid.UUID(str(membership.id)),
            organization_id=uuid.UUID(str(membership.organization_id)),
            user_id=uuid.UUID(str(membership.user_id)),
            role=membership.role,
            status=membership.status,
            manager_user_id=(
                uuid.UUID(str(membership.manager_user_id)) if membership.manager_user_id else None
            ),
            title=membership.title,
            created_at=membership.created_at,
            updated_at=membership.updated_at,
        )


class MembershipListResponse(BaseModel):
    items: list[MembershipResponse]
    meta: PageMeta


class TeamMemberAddRequest(StrictModel):
    user_id: uuid.UUID
    role: TeamRole = TeamRole.TEAM_MEMBER


class TeamMembershipResponse(ResponseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID
    role: TeamRole
    status: MembershipStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, membership: TeamMembership) -> TeamMembershipResponse:
        return cls(
            id=uuid.UUID(str(membership.id)),
            team_id=uuid.UUID(str(membership.team_id)),
            user_id=uuid.UUID(str(membership.user_id)),
            role=membership.role,
            status=membership.status,
            created_at=membership.created_at,
            updated_at=membership.updated_at,
        )


class TeamMembershipListResponse(BaseModel):
    items: list[TeamMembershipResponse]
    meta: PageMeta


# -- agents ---------------------------------------------------------------


class AgentRegisterRequest(StrictModel):
    """Register an agent owned by the caller.

    There is no owner field: the owner is always the authenticated principal, so
    a caller cannot register an agent on someone else's behalf.
    """

    display_name: str = Field(min_length=1, max_length=120)
    runtime_identity: str = Field(min_length=3, max_length=64)
    capabilities: list[Capability] = Field(min_length=1)
    max_token_ttl_seconds: int | None = Field(default=None, ge=1, le=3600)


class AgentResponse(ResponseModel):
    """An agent as its owner sees it.

    The capability set here is the agent's *ceiling*, not a grant. It is only
    ever returned to the owner; a non-owner receives 404.
    """

    id: uuid.UUID
    display_name: str
    runtime_identity: str
    capabilities: list[Capability]
    status: AgentStatus
    max_token_ttl_seconds: int
    version: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None

    @classmethod
    def from_domain(cls, agent: AgentIdentity) -> AgentResponse:
        return cls(
            id=uuid.UUID(str(agent.id)),
            display_name=agent.display_name,
            runtime_identity=str(agent.runtime_identity),
            capabilities=sorted(agent.capabilities),
            status=agent.status,
            max_token_ttl_seconds=agent.max_token_ttl_seconds,
            version=agent.version,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
            revoked_at=agent.revoked_at,
        )


class AgentListResponse(BaseModel):
    items: list[AgentResponse]
    meta: PageMeta


class DelegationGrantRequest(StrictModel):
    """Ask a delegator's consent to be narrowed to these capabilities.

    The request is attenuated against the agent's ceiling by the application
    layer, so asking for more than the agent holds is rejected rather than
    quietly trimmed.
    """

    capabilities: list[Capability] = Field(min_length=1)


class DelegationResponse(ResponseModel):
    """One user's consent for one agent to act, with the narrowed capability set."""

    id: uuid.UUID
    agent_id: uuid.UUID
    capabilities: list[Capability]
    status: DelegationStatus
    created_at: datetime
    revoked_at: datetime | None = None

    @classmethod
    def from_domain(cls, delegation: DelegatedAuthorization) -> DelegationResponse:
        return cls(
            id=uuid.UUID(str(delegation.id)),
            agent_id=uuid.UUID(str(delegation.agent_id)),
            capabilities=sorted(delegation.capabilities),
            status=delegation.status,
            created_at=delegation.created_at,
            revoked_at=delegation.revoked_at,
        )


class AgentTokenIssueRequest(StrictModel):
    """Exchange a delegation for a short-lived, narrower token.

    ``capabilities`` must be a subset of the delegation's set; the TTL is bounded
    by the agent's own maximum, so a caller cannot mint a long-lived credential.
    """

    capabilities: list[Capability] = Field(min_length=1)
    audience: TokenAudience
    ttl_seconds: int = Field(ge=1, le=3600)


class AgentTokenResponse(ResponseModel):
    """A freshly issued token.

    ``secret`` is the raw bearer value. It is returned exactly once, at issuance,
    and never persisted — only its SHA-256 hash is stored — so a later read can
    never recover it. ``token_hash`` is deliberately absent from every response.
    """

    id: uuid.UUID
    agent_id: uuid.UUID
    delegation_id: uuid.UUID
    audience: TokenAudience
    capabilities: list[Capability]
    issued_at: datetime
    expires_at: datetime
    secret: str

    @classmethod
    def from_issued(cls, issued: IssuedToken) -> AgentTokenResponse:
        token = issued.token
        return cls(
            id=uuid.UUID(str(token.id)),
            agent_id=uuid.UUID(str(token.agent_id)),
            delegation_id=uuid.UUID(str(token.delegation_id)),
            audience=token.audience,
            capabilities=sorted(token.capabilities),
            issued_at=token.issued_at,
            expires_at=token.expires_at,
            secret=issued.secret,
        )


class AgentTokenResolveRequest(StrictModel):
    """Resolve a presented token secret for a given audience.

    The audience is part of the token's identity, so a token minted for the tool
    gateway is rejected by the Notes API and vice versa.
    """

    secret: str = Field(min_length=1, max_length=512)
    audience: TokenAudience


class ResolvedAgentIdentity(BaseModel):
    """The agent a token belongs to, as an introspecting caller may see it.

    Deliberately narrower than :class:`AgentResponse`: a token holder is not
    necessarily the agent's owner, and the owner-only view carries the agent's
    *ceiling* — strictly more authority than this token was granted. Echoing it
    here would tell a token holder which capabilities exist to escalate towards.
    """

    id: uuid.UUID
    display_name: str
    runtime_identity: str
    status: AgentStatus

    @classmethod
    def from_domain(cls, agent: AgentIdentity) -> ResolvedAgentIdentity:
        return cls(
            id=uuid.UUID(str(agent.id)),
            display_name=agent.display_name,
            runtime_identity=str(agent.runtime_identity),
            status=agent.status,
        )


class ResolvedAgentTokenResponse(BaseModel):
    """The actor a valid token resolves to, with its effective capabilities.

    ``capabilities`` is what this token carries — never the agent's ceiling.
    """

    agent: ResolvedAgentIdentity
    delegation_id: uuid.UUID
    audience: TokenAudience
    capabilities: list[Capability]
    expires_at: datetime

    @classmethod
    def from_resolved(cls, resolved: ResolvedToken) -> ResolvedAgentTokenResponse:
        return cls(
            agent=ResolvedAgentIdentity.from_domain(resolved.agent),
            delegation_id=uuid.UUID(str(resolved.delegation.id)),
            audience=resolved.token.audience,
            capabilities=sorted(resolved.token.capabilities),
            expires_at=resolved.token.expires_at,
        )


class ApprovalRequestOpenRequest(StrictModel):
    """Ask a human to authorise one sensitive action.

    The agent must already hold the capability; approval gates its *use*, it does
    not grant it.
    """

    capability: Capability
    resource_ref: str = Field(min_length=1, max_length=512)


class ApprovalRevokeRequest(StrictModel):
    """A human withdrawing an approval they previously granted."""

    reason: str | None = Field(default=None, max_length=512)


class ApprovalDecisionRequest(StrictModel):
    """A human's decision on a pending approval request."""

    approve: bool
    reason: str | None = Field(default=None, max_length=512)


class ApprovalRequestResponse(ResponseModel):
    """A pending or decided approval request for one sensitive agent action."""

    id: uuid.UUID
    agent_id: uuid.UUID
    capability: Capability
    resource_ref: str
    status: ApprovalStatus
    requested_at: datetime
    decided_at: datetime | None = None
    decided_by_user_id: uuid.UUID | None = None
    reason: str | None = None
    expires_at: datetime | None = None
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by_user_id: uuid.UUID | None = None
    revoke_reason: str | None = None

    @classmethod
    def from_domain(cls, request: ApprovalRequest) -> ApprovalRequestResponse:
        return cls(
            id=uuid.UUID(str(request.id)),
            agent_id=uuid.UUID(str(request.agent_id)),
            capability=request.capability,
            resource_ref=request.resource_ref,
            status=request.status,
            requested_at=request.requested_at,
            decided_at=request.decided_at,
            decided_by_user_id=(
                uuid.UUID(str(request.decided_by_user_id))
                if request.decided_by_user_id is not None
                else None
            ),
            reason=request.reason,
            expires_at=request.expires_at,
            consumed_at=request.consumed_at,
            revoked_at=request.revoked_at,
            revoked_by_user_id=(
                uuid.UUID(str(request.revoked_by_user_id))
                if request.revoked_by_user_id is not None
                else None
            ),
            revoke_reason=request.revoke_reason,
        )


class ApprovalRequestListResponse(BaseModel):
    items: list[ApprovalRequestResponse]
    meta: PageMeta


class ApprovalAdvisoryResponse(ResponseModel):
    """A provider's second opinion about one approval request.

    Not an approval. ``score`` is an advisory number where higher means "looks
    more routine"; ``None`` means no opinion was formed, and ``abstention_reason``
    then says why. A reviewer still decides the request themselves.
    """

    approval_id: uuid.UUID
    score: float | None = None
    confidence: float
    abstention_reason: AbstentionReason | None = None
    provider_name: str
    context_hash: str
    excluded_context_fields: tuple[str, ...] = ()
    over_cap_context_fields: tuple[str, ...] = ()
    latency_ms: float
    advisory_available: bool

    @classmethod
    def from_domain(cls, advisory: ApprovalAdvisory) -> ApprovalAdvisoryResponse:
        return cls(
            approval_id=uuid.UUID(advisory.approval_id),
            score=advisory.score,
            confidence=advisory.confidence,
            abstention_reason=advisory.abstention_reason,
            provider_name=advisory.provider_name,
            context_hash=advisory.context_hash,
            excluded_context_fields=advisory.excluded_context_fields,
            over_cap_context_fields=advisory.over_cap_context_fields,
            latency_ms=advisory.latency_ms,
            advisory_available=advisory.is_advisory_available,
        )


class WithholdingReportResponse(ResponseModel):
    """Process-wide advisory withholding totals, for operators.

    ``over_cap_rate`` is the aggregate R34 asks for: the fraction of advisories
    that were computed on less context than the caller held. It is reported
    alongside its numerator and denominator so a reader can judge how much the
    rate means at the current volume — ``0.0`` over three advisories and ``0.0``
    over thirty thousand are very different claims.

    Counts only. No field name, value, note id or user id appears here, which is
    why this can be a single process-wide number without disclosing content.
    """

    advisories_total: int
    over_cap_advisories_total: int
    over_cap_fields_total: int
    over_cap_rate: float

    @classmethod
    def from_snapshot(cls, snapshot: WithholdingSnapshot) -> WithholdingReportResponse:
        return cls(
            advisories_total=snapshot.advisories_total,
            over_cap_advisories_total=snapshot.over_cap_advisories_total,
            over_cap_fields_total=snapshot.over_cap_fields_total,
            over_cap_rate=snapshot.over_cap_rate,
        )


# -- tool gateway ---------------------------------------------------------


class ToolInvocationRequest(StrictModel):
    """One governed tool invocation presented by an agent.

    The agent's token is the credential and travels in the ``Authorization``
    header, never here: no identity or authority may come from the body. The
    idempotency key is required so a retried call replays its recorded outcome
    instead of repeating the side effect.
    """

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, object] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ToolInvocationResponse(ResponseModel):
    """The audited outcome of one tool invocation.

    ``replayed`` distinguishes a replayed outcome from a fresh execution, so a
    caller can tell that the side effect did not happen twice. ``output`` is the
    handler's result and is empty for a refusal.
    """

    id: uuid.UUID
    tool: str
    capability: Capability
    status: InvocationStatus
    resource_ref: str
    idempotency_key: str
    invoked_at: datetime
    completed_at: datetime | None = None
    outcome: str | None = None
    replayed: bool = False
    output: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def from_result(cls, result: ToolResult) -> ToolInvocationResponse:
        invocation = result.invocation
        return cls(
            id=uuid.UUID(str(invocation.id)),
            tool=invocation.tool_name,
            capability=invocation.capability,
            status=invocation.status,
            resource_ref=invocation.resource_ref,
            idempotency_key=invocation.idempotency_key,
            invoked_at=invocation.invoked_at,
            completed_at=invocation.completed_at,
            outcome=invocation.outcome,
            replayed=result.replayed,
            output=dict(result.output),
        )


class ToolInvocationAuditResponse(ResponseModel):
    """One row of the tool-invocation audit trail, as the owning human reads it.

    ``arguments_digest`` is returned instead of the arguments themselves: the
    trail must prove *that* a call happened with a given shape without becoming a
    second copy of note content. ``outcome`` carries the gateway's reason for a
    refusal, which is the part an auditor needs most.
    """

    id: uuid.UUID
    agent_id: uuid.UUID
    token_id: uuid.UUID | None = None
    tool: str
    capability: Capability
    status: InvocationStatus
    resource_ref: str
    idempotency_key: str
    arguments_digest: str
    approval_request_id: uuid.UUID | None = None
    invoked_at: datetime
    completed_at: datetime | None = None
    outcome: str | None = None
    chain_sequence: int
    previous_hash: str
    intent_hash: str
    outcome_hash: str | None = None

    @classmethod
    def from_domain(cls, invocation: ToolInvocation) -> ToolInvocationAuditResponse:
        return cls(
            id=uuid.UUID(str(invocation.id)),
            agent_id=uuid.UUID(str(invocation.agent_id)),
            token_id=(
                uuid.UUID(str(invocation.token_id)) if invocation.token_id is not None else None
            ),
            tool=invocation.tool_name,
            capability=invocation.capability,
            status=invocation.status,
            resource_ref=invocation.resource_ref,
            idempotency_key=invocation.idempotency_key,
            arguments_digest=invocation.arguments_digest,
            approval_request_id=(
                uuid.UUID(str(invocation.approval_request_id))
                if invocation.approval_request_id is not None
                else None
            ),
            invoked_at=invocation.invoked_at,
            completed_at=invocation.completed_at,
            outcome=invocation.outcome,
            chain_sequence=invocation.chain_sequence,
            previous_hash=invocation.previous_hash,
            intent_hash=invocation.intent_hash,
            outcome_hash=invocation.outcome_hash,
        )


# -- durable runs ---------------------------------------------------------


class PlannedStepRequest(StrictModel):
    """One step of a proposed plan.

    ``capability`` is **not** accepted: the capability behind a tool is a property
    of the tool catalog, so letting a caller declare it would let a plan claim a
    weaker authority than the step it schedules. The application layer derives it
    and refuses a plan whose tool it does not know.
    """

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, object] = Field(default_factory=dict)
    resource_ref: str = Field(default="", max_length=512)


class PlanAgentRunRequest(StrictModel):
    """A durable plan presented by an agent, authenticated by its own token."""

    steps: list[PlannedStepRequest] = Field(min_length=1, max_length=100)


class AdvanceAgentRunRequest(StrictModel):
    """Ask a run to proceed. ``max_steps`` bounds one request's work."""

    max_steps: int = Field(default=100, ge=1, le=1000)


class ResolveInterruptedStepRequest(StrictModel):
    """The owner's ruling on an ambiguous step.

    ``effect_landed`` is required and explicit: the domain will not infer whether
    a crashed step applied its side effect, because either guess is wrong half the
    time.
    """

    effect_landed: bool
    outcome: str | None = Field(default=None, max_length=500)


class AbortAgentRunRequest(StrictModel):
    """Stop a run permanently, with an optional recorded reason."""

    reason: str | None = Field(default=None, max_length=500)


class RunStepResponse(ResponseModel):
    """One step as the owner sees it, including how far it got."""

    id: uuid.UUID
    sequence: int
    tool: str
    capability: Capability
    idempotency_key: str
    resource_ref: str
    status: StepStatus
    outcome: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_domain(cls, step: RunStep) -> RunStepResponse:
        return cls(
            id=uuid.UUID(str(step.id)),
            sequence=step.sequence,
            tool=step.tool_name,
            capability=step.capability,
            idempotency_key=step.idempotency_key,
            resource_ref=step.resource_ref,
            status=step.status,
            outcome=step.outcome,
            started_at=step.started_at,
            completed_at=step.completed_at,
        )


class AgentRunResponse(ResponseModel):
    """A durable run. Step arguments are not echoed; the digest and plan are."""

    id: uuid.UUID
    agent_id: uuid.UUID
    delegation_id: uuid.UUID | None = None
    status: RunStatus
    version: int
    steps: list[RunStepResponse]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: str | None = None

    @classmethod
    def from_domain(cls, run: AgentRun) -> AgentRunResponse:
        return cls(
            id=uuid.UUID(str(run.id)),
            agent_id=uuid.UUID(str(run.agent_id)),
            delegation_id=(
                uuid.UUID(str(run.delegation_id)) if run.delegation_id is not None else None
            ),
            status=run.status,
            version=run.version,
            steps=[RunStepResponse.from_domain(step) for step in run.ordered_steps],
            created_at=run.created_at,
            updated_at=run.updated_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            last_error=run.last_error,
        )


class StepOutcomeResponse(ResponseModel):
    """What one executed step produced in this request."""

    sequence: int
    tool: str
    output: dict[str, object] = Field(default_factory=dict)


class RunAdvanceResponse(ResponseModel):
    """The run after advancing, plus what this request executed.

    ``halted_reason`` is set when the run stopped without completing, so a caller
    can tell "finished" from "waiting on a human" from "a step failed".
    """

    run: AgentRunResponse
    outcomes: list[StepOutcomeResponse] = Field(default_factory=list)
    halted_reason: str | None = None

    @classmethod
    def from_domain(cls, advance: RunAdvance) -> RunAdvanceResponse:
        return cls(
            run=AgentRunResponse.from_domain(advance.run),
            outcomes=[
                StepOutcomeResponse(
                    sequence=outcome.sequence, tool=outcome.tool_name, output=outcome.output
                )
                for outcome in advance.outcomes
            ],
            halted_reason=advance.halted_reason,
        )


class AgentRunListResponse(BaseModel):
    items: list[AgentRunResponse]
    meta: PageMeta


class ToolInvocationAuditListResponse(BaseModel):
    items: list[ToolInvocationAuditResponse]
    meta: PageMeta


class ChainBreakResponse(BaseModel):
    """Where the recomputation first disagreed with what is stored."""

    sequence: int
    invocation_id: str
    reason: str


class ChainVerdictResponse(BaseModel):
    """The result of recomputing an agent's audit chain.

    ``verified`` is the answer; ``checked`` is the evidence behind it, so
    "verified 0 entries" cannot be mistaken for "verified a trail". A break is
    reported as data (``200`` with ``verified: false``), not as an error: the
    request succeeded, and the finding is the payload. An error status would be
    indistinguishable from a request that failed to run at all.
    """

    verified: bool
    checked: int
    reason: str | None = None
    first_break: ChainBreakResponse | None = None

    @classmethod
    def from_domain(cls, verdict: ChainVerdict) -> ChainVerdictResponse:
        first = verdict.first_break
        return cls(
            verified=verdict.verified,
            checked=verdict.checked,
            reason=verdict.reason,
            first_break=(
                ChainBreakResponse(
                    sequence=first.sequence,
                    invocation_id=first.invocation_id,
                    reason=first.reason,
                )
                if first is not None
                else None
            ),
        )


# -- envelopes ------------------------------------------------------------


class NoteListResponse(BaseModel):
    items: list[NoteSummaryResponse]
    meta: PageMeta


class UserListResponse(BaseModel):
    items: list[UserResponse]
    meta: PageMeta


class DemoIdentityResponse(ResponseModel):
    """A selectable development demo identity.

    Deliberately the same shape as :class:`UserResponse` (id, email, display
    name) so the client can treat a demo identity and a shareable user
    uniformly. It exposes nothing beyond what is needed to pick an identity.
    """

    id: uuid.UUID
    email: str
    display_name: str

    @classmethod
    def from_domain(cls, user: User) -> DemoIdentityResponse:
        return cls(
            id=uuid.UUID(str(user.id)), email=str(user.email), display_name=user.display_name
        )


class DemoIdentityListResponse(BaseModel):
    """The set of demo identities a client may select, with a header hint.

    ``header`` tells the client exactly which request header selects an
    identity, so the frontend never hardcodes it.
    """

    enabled: bool
    header: str
    items: list[DemoIdentityResponse]
    meta: PageMeta


class PermissionListResponse(BaseModel):
    items: list[PermissionResponse]
    meta: PageMeta


class ErrorBody(BaseModel):
    """A single error object inside the standard error envelope."""

    code: str
    message: str
    details: list[dict[str, object]] | None = None


class ErrorResponse(BaseModel):
    """The standard error envelope returned for every failure."""

    error: ErrorBody
    request_id: str | None = None
