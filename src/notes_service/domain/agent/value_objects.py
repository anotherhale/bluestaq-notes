"""Value objects for the agent bounded context.

An agent is a **service principal**, never an employee account. Everything an
agent may do is expressed as a :class:`Capability` and must be derived by
attenuation from a human delegator or an explicit service-agent policy. The
value objects here are the vocabulary the rest of the context is written in.

Two distinctions are load-bearing:

* **Sensitive capabilities** (:data:`SENSITIVE_CAPABILITIES`) are the ones that
  change who can see or destroy data. They require an explicit human approval
  that the agent cannot grant itself.
* **Non-delegable capabilities** (:data:`NON_DELEGABLE_CAPABILITIES`) may only
  come from a service-agent policy. A human can never hand these to an agent
  through ordinary delegation, because doing so would let a single consent
  escalate an agent to organization-wide reach.
"""

from __future__ import annotations

import re
import uuid
from enum import StrEnum

from notes_service.domain.errors import ValidationError

# A runtime identity is the stable name a deployed agent process presents
# (for example ``svc-notes-summarizer``). It is not a credential; it names the
# workload so operators can correlate logs and rotate policy per deployment.
_RUNTIME_IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$")

_MAX_DISPLAY_NAME = 120

# Tokens are short-lived by policy. The ceiling is enforced per-agent so a
# misconfigured agent cannot mint a long-lived credential.
DEFAULT_MAX_TOKEN_TTL_SECONDS = 300
HARD_MAX_TOKEN_TTL_SECONDS = 3600


class AgentId(uuid.UUID):
    """Strongly-typed agent identifier."""

    __slots__ = ()

    @classmethod
    def new(cls) -> AgentId:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str | uuid.UUID) -> AgentId:
        if isinstance(value, AgentId):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid agent id: {value!r}") from exc


class DelegatedAuthorizationId(uuid.UUID):
    """Identifier for one user's consent to delegate authority to an agent."""

    __slots__ = ()

    @classmethod
    def new(cls) -> DelegatedAuthorizationId:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str | uuid.UUID) -> DelegatedAuthorizationId:
        if isinstance(value, DelegatedAuthorizationId):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid delegated authorization id: {value!r}") from exc


class AgentTokenId(uuid.UUID):
    """Identifier for an issued agent access token."""

    __slots__ = ()

    @classmethod
    def new(cls) -> AgentTokenId:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str | uuid.UUID) -> AgentTokenId:
        if isinstance(value, AgentTokenId):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid agent token id: {value!r}") from exc


class ApprovalRequestId(uuid.UUID):
    """Identifier for a pending human approval of a sensitive agent action."""

    __slots__ = ()

    @classmethod
    def new(cls) -> ApprovalRequestId:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str | uuid.UUID) -> ApprovalRequestId:
        if isinstance(value, ApprovalRequestId):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid approval request id: {value!r}") from exc


class RuntimeIdentity(str):
    """The stable workload name an agent presents, e.g. ``svc-summarizer``."""

    __slots__ = ()

    def __new__(cls, value: str) -> RuntimeIdentity:
        if not isinstance(value, str):
            raise ValidationError("runtime identity must be a string")
        candidate = value.strip().lower()
        if not _RUNTIME_IDENTITY_RE.match(candidate):
            raise ValidationError(
                "runtime identity must be 3-64 chars of lowercase letters, digits, "
                "'.', '_' or '-', starting and ending alphanumeric"
            )
        return super().__new__(cls, candidate)


class AgentStatus(StrEnum):
    """Lifecycle state of an agent principal.

    ``SUSPENDED`` and ``REVOKED`` agents cannot be issued tokens. ``REVOKED`` is
    terminal: revocation is the emergency stop and must not be undone by an
    in-place status flip, which would let a compromised agent be revived
    silently.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"

    @property
    def is_operational(self) -> bool:
        return self is AgentStatus.ACTIVE


class Capability(StrEnum):
    """A discrete thing an agent is allowed to do.

    The set is deliberately small and maps to the typed tool surface in the
    agentic expansion spec. There is no generic "CRUD" capability: each entry is
    a named operation so authorization and audit can reason about it.
    """

    SEARCH_NOTES = "search_notes"
    READ_NOTE = "read_note"
    CREATE_NOTE = "create_note"
    UPDATE_NOTE = "update_note"
    APPEND_NOTE = "append_to_note"
    LIST_SHARED_NOTES = "list_shared_notes"
    LIST_TEAMS = "list_teams"
    # Sensitive: these change visibility or destroy data.
    SHARE_NOTE = "share_note"
    REVOKE_SHARE = "revoke_share"
    DELETE_NOTE = "delete_note"
    # Organization-wide reach; service-agent policy only.
    ORG_SEARCH = "org_search"


# Capabilities that require an explicit human approval before use. An agent can
# request them, but never approve its own request.
SENSITIVE_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.SHARE_NOTE,
        Capability.REVOKE_SHARE,
        Capability.DELETE_NOTE,
        Capability.ORG_SEARCH,
    }
)

# Capabilities a human cannot delegate. Granting organization-wide search on a
# single user's consent would let one click widen an agent's blast radius beyond
# anything that user can do themselves, so it is policy-only.
NON_DELEGABLE_CAPABILITIES: frozenset[Capability] = frozenset({Capability.ORG_SEARCH})


class TokenAudience(StrEnum):
    """The resource server a token is minted for.

    A token for the tool gateway must not be replayable against the Notes API and
    vice versa, so the audience is part of the token's identity, not metadata.
    """

    NOTES_API = "notes-api"
    TOOL_GATEWAY = "tool-gateway"


class DelegationStatus(StrEnum):
    """State of a user's consent to delegate authority to an agent."""

    ACTIVE = "active"
    REVOKED = "revoked"


class ApprovalStatus(StrEnum):
    """State of a human approval for a sensitive agent action.

    ``REVOKED`` is distinct from ``DENIED``: a denial is a refusal to grant,
    while a revocation withdraws a grant that had already been made. The
    security architecture (§11) names expired, revoked, and invalidated as
    separate states, and all of them block execution. Collapsing revocation into
    denial would erase the difference between "never allowed" and "allowed, then
    withdrawn" from the audit record.
    """

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"
