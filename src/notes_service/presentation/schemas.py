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

from notes_service.application.use_cases.agents import IssuedToken, ResolvedToken
from notes_service.application.use_cases.organization import OrganizationView
from notes_service.domain.agent.delegation import DelegatedAuthorization
from notes_service.domain.agent.entities import AgentIdentity, ApprovalRequest
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
        )


class ApprovalRequestListResponse(BaseModel):
    items: list[ApprovalRequestResponse]
    meta: PageMeta


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
