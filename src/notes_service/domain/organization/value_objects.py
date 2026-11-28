"""Organization value objects: identifiers, slugs, statuses, and roles.

The organization hierarchy is the *tenant* boundary: a user's membership in an
organization is what scopes every enterprise resource. Value objects here are
validated at construction so an invalid slug or an unknown role can never reach
persistence.

Roles are enum-backed for now. They are deliberately coarse: fine-grained
policy (scoped grants, ABAC) is expected to arrive later behind the same
vocabulary, so callers should not depend on a role string beyond these members.
"""

from __future__ import annotations

import re
import uuid
from enum import StrEnum
from typing import Self

from notes_service.domain.errors import ValidationError

# Canonical slug grammar: lower-case alphanumerics separated by single hyphens.
# Anchored so it cannot be satisfied by a substring, and it forbids leading,
# trailing, or doubled hyphens (which would make URLs and lookups ambiguous).
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

MAX_SLUG_LENGTH = 63
MAX_NAME_LENGTH = 120

# A hierarchy deeper than this is almost certainly a modelling error, and an
# unbounded depth makes every ancestry walk (and cycle check) expensive.
MAX_HIERARCHY_DEPTH = 10


class Slug(str):
    """A validated, URL-safe organization/unit/team slug.

    Slugs are unique per scope (organization slug is globally unique; unit and
    team slugs are unique within their parent). Normalisation is limited to
    trimming surrounding whitespace: silently lower-casing or transliterating
    would let two different inputs collide on one slug.
    """

    __slots__ = ()

    def __new__(cls, value: str) -> Slug:
        if not isinstance(value, str):
            raise ValidationError("slug must be a string")
        candidate = value.strip()
        if not candidate:
            raise ValidationError("slug must not be empty")
        if len(candidate) > MAX_SLUG_LENGTH:
            raise ValidationError(f"slug must be at most {MAX_SLUG_LENGTH} characters")
        if not _SLUG_RE.match(candidate):
            raise ValidationError(
                "slug must be lower-case alphanumerics separated by single hyphens"
            )
        return super().__new__(cls, candidate)


class OrganizationName(str):
    """A validated human-readable name for an organization, unit, or team."""

    __slots__ = ()

    def __new__(cls, value: str) -> OrganizationName:
        if not isinstance(value, str):
            raise ValidationError("name must be a string")
        candidate = value.strip()
        if not candidate:
            raise ValidationError("name must not be empty")
        if len(candidate) > MAX_NAME_LENGTH:
            raise ValidationError(f"name must be at most {MAX_NAME_LENGTH} characters")
        if "\x00" in candidate:
            raise ValidationError("name must not contain NUL characters")
        return super().__new__(cls, candidate)


class _DomainId(uuid.UUID):
    """Base for strongly-typed organization identifiers.

    Subclasses only differ by the ``label`` used in validation errors, so the
    parse logic lives here once.
    """

    __slots__ = ()
    label = "id"

    @classmethod
    def new(cls: type[Self]) -> Self:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls: type[Self], value: str | uuid.UUID) -> Self:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid {cls.label}: {value!r}") from exc


class OrganizationId(_DomainId):
    __slots__ = ()
    label = "organization id"


class OrganizationUnitId(_DomainId):
    __slots__ = ()
    label = "organization unit id"


class TeamId(_DomainId):
    __slots__ = ()
    label = "team id"


class OrganizationMembershipId(_DomainId):
    __slots__ = ()
    label = "organization membership id"


class TeamMembershipId(_DomainId):
    __slots__ = ()
    label = "team membership id"


class OrganizationStatus(StrEnum):
    """Lifecycle of an organization (the tenant itself)."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"

    @property
    def is_operational(self) -> bool:
        """Only an active organization may grant access to its resources."""
        return self is OrganizationStatus.ACTIVE


class UnitStatus(StrEnum):
    """Lifecycle of an organization unit (division, department, ...)."""

    ACTIVE = "active"
    ARCHIVED = "archived"

    @property
    def is_operational(self) -> bool:
        return self is UnitStatus.ACTIVE


class TeamStatus(StrEnum):
    """Lifecycle of a team."""

    ACTIVE = "active"
    ARCHIVED = "archived"

    @property
    def is_operational(self) -> bool:
        return self is TeamStatus.ACTIVE


class MembershipStatus(StrEnum):
    """Lifecycle of a person's membership in an organization or team.

    ``DEPROVISIONED`` is terminal and represents a completed offboarding: the
    person keeps no access at all. ``SUSPENDED`` is a reversible pause.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEPROVISIONED = "deprovisioned"

    @property
    def grants_access(self) -> bool:
        """Only an active membership contributes any authority."""
        return self is MembershipStatus.ACTIVE


class OrgUnitType(StrEnum):
    """The kind of organizational node.

    This is descriptive metadata, not an authorization input: two units of
    different types behave identically. It exists so the org chart can be
    rendered and filtered meaningfully.
    """

    DIVISION = "division"
    DEPARTMENT = "department"
    BRANCH = "branch"
    PROGRAM = "program"


class OrganizationRole(StrEnum):
    """A person's role inside one organization.

    Roles are ranked for *administrative* comparison only. Holding an
    administrative role never implies the ability to read other people's private
    note content — content access is always decided by the notes policy.
    """

    ORG_OWNER = "org_owner"
    ORG_ADMIN = "org_admin"
    ORG_SECURITY_ADMIN = "org_security_admin"
    ORG_AUDITOR = "org_auditor"
    ORG_MEMBER = "org_member"

    @property
    def rank(self) -> int:
        """Higher is more privileged; used only for explicit, audited checks."""
        return _ORG_ROLE_RANK[self]

    def at_least(self, other: OrganizationRole) -> bool:
        return self.rank >= other.rank

    @property
    def is_administrative(self) -> bool:
        """True for roles that may change organization structure or membership."""
        return self in {
            OrganizationRole.ORG_OWNER,
            OrganizationRole.ORG_ADMIN,
        }


_ORG_ROLE_RANK: dict[OrganizationRole, int] = {
    OrganizationRole.ORG_MEMBER: 1,
    OrganizationRole.ORG_AUDITOR: 2,
    OrganizationRole.ORG_SECURITY_ADMIN: 3,
    OrganizationRole.ORG_ADMIN: 4,
    OrganizationRole.ORG_OWNER: 5,
}


class TeamRole(StrEnum):
    """A person's role inside one team."""

    TEAM_LEAD = "team_lead"
    TEAM_MEMBER = "team_member"

    @property
    def is_lead(self) -> bool:
        return self is TeamRole.TEAM_LEAD


class OrgPermission(StrEnum):
    """A discrete administrative capability inside an organization.

    Capabilities are the unit of authorization for the admin console. Mapping
    roles to capabilities in one place (see ``ROLE_CAPABILITIES``) keeps the
    decision auditable and lets a future policy engine replace the mapping
    without touching call sites.
    """

    VIEW_ORGANIZATION = "view_organization"
    MANAGE_ORGANIZATION = "manage_organization"
    MANAGE_UNITS = "manage_units"
    MANAGE_TEAMS = "manage_teams"
    MANAGE_MEMBERSHIPS = "manage_memberships"
    MANAGE_ROLES = "manage_roles"
    VIEW_AUDIT = "view_audit"
    #: Supply the *second* signature on a two-person agent-action approval.
    #: Deliberately separate from the structure-management capabilities: a
    #: role can be trusted to co-sign a sensitive agent action without being
    #: able to reshape the organization, and vice versa.
    APPROVE_AGENT_ACTION = "approve_agent_action"


# The role -> capability matrix. ORG_ADMIN can do everything administrative
# except change ownership (that is ORG_OWNER only, and there is no such
# endpoint yet). ORG_SECURITY_ADMIN and ORG_AUDITOR are read-only over
# structure and membership, which is what makes separation of duties real.
ROLE_CAPABILITIES: dict[OrganizationRole, frozenset[OrgPermission]] = {
    OrganizationRole.ORG_OWNER: frozenset(OrgPermission),
    OrganizationRole.ORG_ADMIN: frozenset(
        {
            OrgPermission.VIEW_ORGANIZATION,
            OrgPermission.MANAGE_ORGANIZATION,
            OrgPermission.MANAGE_UNITS,
            OrgPermission.MANAGE_TEAMS,
            OrgPermission.MANAGE_MEMBERSHIPS,
            OrgPermission.MANAGE_ROLES,
            OrgPermission.VIEW_AUDIT,
            OrgPermission.APPROVE_AGENT_ACTION,
        }
    ),
    OrganizationRole.ORG_SECURITY_ADMIN: frozenset(
        {
            OrgPermission.VIEW_ORGANIZATION,
            OrgPermission.VIEW_AUDIT,
        }
    ),
    OrganizationRole.ORG_AUDITOR: frozenset(
        {
            OrgPermission.VIEW_ORGANIZATION,
            OrgPermission.VIEW_AUDIT,
        }
    ),
    OrganizationRole.ORG_MEMBER: frozenset({OrgPermission.VIEW_ORGANIZATION}),
}


def capabilities_for(role: OrganizationRole) -> frozenset[OrgPermission]:
    """Return the administrative capabilities granted by ``role``."""
    return ROLE_CAPABILITIES[role]


__all__ = [
    "MAX_HIERARCHY_DEPTH",
    "MAX_NAME_LENGTH",
    "MAX_SLUG_LENGTH",
    "ROLE_CAPABILITIES",
    "MembershipStatus",
    "OrgPermission",
    "OrgUnitType",
    "OrganizationId",
    "OrganizationMembershipId",
    "OrganizationName",
    "OrganizationRole",
    "OrganizationStatus",
    "Slug",
    "TeamId",
    "TeamMembershipId",
    "TeamRole",
    "TeamStatus",
    "UnitStatus",
    "capabilities_for",
]
