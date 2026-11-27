"""Organization aggregate: organizations, units, teams, and memberships.

Design notes that are easy to get wrong:

* **The organization is the tenant boundary.** Every enterprise resource is
  reached through a membership. Nothing here grants access to note *content*:
  an ``ORG_ADMIN`` can restructure the org chart without thereby reading a
  colleague's private note. Content authorization stays with the notes policy.
* **Units and teams form trees.** A unit may have a parent unit inside the same
  organization; a team may have a parent team. ``attach``/``move`` validate the
  resulting ancestry so a cycle can never be persisted. The tree is bounded by
  :data:`MAX_HIERARCHY_DEPTH`.
* **The reporting line lives on the organization membership**, not in a separate
  global table. A manager pointer is only meaningful inside one organization, so
  scoping it to the membership removes an entire class of cross-tenant bugs.
* **Aggregates are small and self-validating.** State transitions
  (``archive``, ``suspend``, ``change_role``) raise
  :class:`~notes_service.domain.errors.InvariantViolation` rather than trusting
  the caller, so a bug in a use case cannot persist an invalid org chart.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.organization.value_objects import (
    MAX_HIERARCHY_DEPTH,
    MembershipStatus,
    OrganizationId,
    OrganizationMembershipId,
    OrganizationName,
    OrganizationRole,
    OrganizationStatus,
    OrganizationUnitId,
    OrgUnitType,
    Slug,
    TeamId,
    TeamMembershipId,
    TeamRole,
    TeamStatus,
    UnitStatus,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Versioned:
    """Mixin for optimistic concurrency.

    Every mutation bumps ``version``; a repository write must be conditional on
    the version the caller read. That is what makes concurrent admin edits a
    ``409`` instead of a silent last-write-wins.
    """

    version: int

    def _bump(self, now: datetime | None) -> None:
        self.version += 1
        self.updated_at = now or _utcnow()


@dataclass(slots=True)
class Organization(_Versioned):
    """The tenant. Owns units, teams, and memberships."""

    id: OrganizationId
    slug: Slug
    name: OrganizationName
    status: OrganizationStatus = OrganizationStatus.ACTIVE
    version: int = 1
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def create(
        cls,
        *,
        slug: Slug | str,
        name: OrganizationName | str,
        organization_id: OrganizationId | None = None,
        status: OrganizationStatus = OrganizationStatus.ACTIVE,
    ) -> Organization:
        return cls(
            id=organization_id or OrganizationId.new(),
            slug=Slug(slug),
            name=OrganizationName(name),
            status=status,
        )

    @property
    def is_operational(self) -> bool:
        """A suspended or archived organization grants no access at all."""
        return self.status.is_operational

    def rename(self, *, name: OrganizationName | str, now: datetime | None = None) -> None:
        self.name = OrganizationName(name)
        self._bump(now)

    def change_slug(self, *, slug: Slug | str, now: datetime | None = None) -> None:
        self.slug = Slug(slug)
        self._bump(now)

    def change_status(self, *, status: OrganizationStatus, now: datetime | None = None) -> None:
        if status is self.status:
            return
        if self.status is OrganizationStatus.ARCHIVED:
            raise InvariantViolation("an archived organization cannot change status")
        self.status = status
        self._bump(now)


@dataclass(slots=True)
class OrganizationUnit(_Versioned):
    """A division, department, branch, or program inside one organization.

    ``parent_id`` is ``None`` for a root unit. ``path`` is a materialised list of
    ancestor ids (root first) supplied by the repository so the domain can
    reject cycles and depth violations without loading the whole tree.
    """

    id: OrganizationUnitId
    organization_id: OrganizationId
    name: OrganizationName
    slug: Slug
    unit_type: OrgUnitType = OrgUnitType.DEPARTMENT
    parent_id: OrganizationUnitId | None = None
    status: UnitStatus = UnitStatus.ACTIVE
    version: int = 1
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def create(
        cls,
        *,
        organization_id: OrganizationId,
        name: OrganizationName | str,
        slug: Slug | str,
        unit_type: OrgUnitType = OrgUnitType.DEPARTMENT,
        parent_id: OrganizationUnitId | None = None,
        unit_id: OrganizationUnitId | None = None,
        status: UnitStatus = UnitStatus.ACTIVE,
    ) -> OrganizationUnit:
        return cls(
            id=unit_id or OrganizationUnitId.new(),
            organization_id=organization_id,
            name=OrganizationName(name),
            slug=Slug(slug),
            unit_type=unit_type,
            parent_id=parent_id,
            status=status,
        )

    @property
    def is_operational(self) -> bool:
        return self.status.is_operational

    def rename(self, *, name: OrganizationName | str, now: datetime | None = None) -> None:
        self.name = OrganizationName(name)
        self._bump(now)

    def change_type(self, *, unit_type: OrgUnitType, now: datetime | None = None) -> None:
        self.unit_type = unit_type
        self._bump(now)

    def assert_can_attach_to(
        self, *, parent_id: OrganizationUnitId | None, parent_path: list[OrganizationUnitId]
    ) -> None:
        """Validate moving this unit under ``parent_id``.

        ``parent_path`` is the ancestor chain of the prospective parent, root
        first, *excluding* the parent itself. Rejecting ``self`` in that chain is
        what prevents a cycle: a unit can never become its own descendant.
        """
        if parent_id is None:
            return
        if parent_id == self.id:
            raise InvariantViolation("a unit cannot be its own parent")
        if self.id in parent_path:
            raise InvariantViolation("moving this unit would create a cycle")
        if len(parent_path) + 2 > MAX_HIERARCHY_DEPTH:
            raise InvariantViolation(
                f"organization hierarchy depth must not exceed {MAX_HIERARCHY_DEPTH}"
            )

    def move_to(
        self,
        *,
        parent_id: OrganizationUnitId | None,
        parent_path: list[OrganizationUnitId],
        now: datetime | None = None,
    ) -> None:
        self.assert_can_attach_to(parent_id=parent_id, parent_path=parent_path)
        self.parent_id = parent_id
        self._bump(now)

    def archive(self, *, now: datetime | None = None) -> None:
        self.status = UnitStatus.ARCHIVED
        self._bump(now)

    def reactivate(self, *, now: datetime | None = None) -> None:
        self.status = UnitStatus.ACTIVE
        self._bump(now)


@dataclass(slots=True)
class Team(_Versioned):
    """A working group inside a unit of one organization.

    Teams always belong to a unit: a team floating directly under the
    organization would duplicate the unit level, and the spec explicitly warns
    against multiple competing hierarchy models.
    """

    id: TeamId
    organization_id: OrganizationId
    unit_id: OrganizationUnitId
    name: OrganizationName
    slug: Slug
    parent_team_id: TeamId | None = None
    status: TeamStatus = TeamStatus.ACTIVE
    version: int = 1
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def create(
        cls,
        *,
        organization_id: OrganizationId,
        unit_id: OrganizationUnitId,
        name: OrganizationName | str,
        slug: Slug | str,
        parent_team_id: TeamId | None = None,
        team_id: TeamId | None = None,
        status: TeamStatus = TeamStatus.ACTIVE,
    ) -> Team:
        return cls(
            id=team_id or TeamId.new(),
            organization_id=organization_id,
            unit_id=unit_id,
            name=OrganizationName(name),
            slug=Slug(slug),
            parent_team_id=parent_team_id,
            status=status,
        )

    @property
    def is_operational(self) -> bool:
        return self.status.is_operational

    def rename(self, *, name: OrganizationName | str, now: datetime | None = None) -> None:
        self.name = OrganizationName(name)
        self._bump(now)

    def move_to_unit(self, *, unit_id: OrganizationUnitId, now: datetime | None = None) -> None:
        self.unit_id = unit_id
        self._bump(now)

    def set_parent(
        self,
        *,
        parent_team_id: TeamId | None,
        parent_path: list[TeamId],
        now: datetime | None = None,
    ) -> None:
        if parent_team_id is not None:
            if parent_team_id == self.id:
                raise InvariantViolation("a team cannot be its own parent")
            if self.id in parent_path:
                raise InvariantViolation("moving this team would create a cycle")
            if len(parent_path) + 2 > MAX_HIERARCHY_DEPTH:
                raise InvariantViolation(
                    f"team hierarchy depth must not exceed {MAX_HIERARCHY_DEPTH}"
                )
        self.parent_team_id = parent_team_id
        self._bump(now)

    def archive(self, *, now: datetime | None = None) -> None:
        self.status = TeamStatus.ARCHIVED
        self._bump(now)

    def reactivate(self, *, now: datetime | None = None) -> None:
        self.status = TeamStatus.ACTIVE
        self._bump(now)


@dataclass(slots=True)
class OrganizationMembership:
    """A person's membership in an organization, with their org role.

    The membership carries the reporting line (``manager_user_id``) because a
    manager relationship is only meaningful within one organization. It is
    validated against the same organization's active memberships by the use
    case, which is also where reporting-line cycles are rejected.
    """

    id: OrganizationMembershipId
    organization_id: OrganizationId
    user_id: str
    role: OrganizationRole = OrganizationRole.ORG_MEMBER
    status: MembershipStatus = MembershipStatus.ACTIVE
    manager_user_id: str | None = None
    title: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    MAX_TITLE_LENGTH = 120

    def __post_init__(self) -> None:
        # ``user_id`` is declared ``str`` but callers naturally pass ``UserId``
        # (a UUID subclass). Normalise here so the in-memory entity and the
        # row loaded back from the database compare equal; otherwise an
        # entity's identity silently depends on whether it has been round-tripped.
        self.user_id = str(self.user_id)
        if self.manager_user_id is not None:
            self.manager_user_id = str(self.manager_user_id)
        if self.title is not None:
            title = self.title.strip()
            if not title:
                self.title = None
            else:
                if len(title) > self.MAX_TITLE_LENGTH:
                    raise ValidationError(
                        f"title must be at most {self.MAX_TITLE_LENGTH} characters"
                    )
                self.title = title
        if self.manager_user_id is not None and self.manager_user_id == self.user_id:
            raise InvariantViolation("a person cannot be their own manager")
        if self.status is MembershipStatus.DEPROVISIONED and self.ended_at is None:
            self.ended_at = _utcnow()

    @classmethod
    def grant(
        cls,
        *,
        organization_id: OrganizationId,
        user_id: str | uuid.UUID,
        role: OrganizationRole = OrganizationRole.ORG_MEMBER,
        manager_user_id: str | uuid.UUID | None = None,
        title: str | None = None,
        membership_id: OrganizationMembershipId | None = None,
        status: MembershipStatus = MembershipStatus.ACTIVE,
    ) -> OrganizationMembership:
        return cls(
            id=membership_id or OrganizationMembershipId.new(),
            organization_id=organization_id,
            user_id=str(user_id),
            role=role,
            status=status,
            manager_user_id=None if manager_user_id is None else str(manager_user_id),
            title=title,
            started_at=_utcnow() if status.grants_access else None,
        )

    @property
    def grants_access(self) -> bool:
        return self.status.grants_access

    def change_role(self, *, role: OrganizationRole, now: datetime | None = None) -> None:
        if not self.grants_access:
            raise InvariantViolation("cannot change the role of an inactive membership")
        self.role = role
        self.updated_at = now or _utcnow()

    def assign_manager(
        self, *, manager_user_id: str | uuid.UUID | None, now: datetime | None = None
    ) -> None:
        if manager_user_id is not None and str(manager_user_id) == self.user_id:
            raise InvariantViolation("a person cannot be their own manager")
        self.manager_user_id = None if manager_user_id is None else str(manager_user_id)
        self.updated_at = now or _utcnow()

    def suspend(self, *, now: datetime | None = None) -> None:
        if self.status is MembershipStatus.DEPROVISIONED:
            raise InvariantViolation("a deprovisioned membership cannot be suspended")
        self.status = MembershipStatus.SUSPENDED
        self.updated_at = now or _utcnow()

    def reactivate(self, *, now: datetime | None = None) -> None:
        if self.status is MembershipStatus.DEPROVISIONED:
            raise InvariantViolation("a deprovisioned membership cannot be reactivated")
        self.status = MembershipStatus.ACTIVE
        self.started_at = self.started_at or _utcnow()
        self.updated_at = now or _utcnow()

    def deprovision(self, *, now: datetime | None = None) -> None:
        """Terminal offboarding. Clears the reporting line so no dangling manager
        pointer remains, and stamps ``ended_at``."""
        if self.status is MembershipStatus.DEPROVISIONED:
            return
        self.status = MembershipStatus.DEPROVISIONED
        self.manager_user_id = None
        self.ended_at = now or _utcnow()
        self.updated_at = self.ended_at


@dataclass(slots=True)
class TeamMembership:
    """A person's membership in one team, with their team role."""

    id: TeamMembershipId
    team_id: TeamId
    user_id: str
    role: TeamRole = TeamRole.TEAM_MEMBER
    status: MembershipStatus = MembershipStatus.ACTIVE
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        # See OrganizationMembership.__post_init__ for why this normalisation
        # matters: an entity must not change identity across a DB round trip.
        self.user_id = str(self.user_id)

    @classmethod
    def grant(
        cls,
        *,
        team_id: TeamId,
        user_id: str,
        role: TeamRole = TeamRole.TEAM_MEMBER,
        membership_id: TeamMembershipId | None = None,
        status: MembershipStatus = MembershipStatus.ACTIVE,
    ) -> TeamMembership:
        return cls(
            id=membership_id or TeamMembershipId.new(),
            team_id=team_id,
            user_id=user_id,
            role=role,
            status=status,
        )

    @property
    def grants_access(self) -> bool:
        return self.status.grants_access

    def change_role(self, *, role: TeamRole, now: datetime | None = None) -> None:
        if not self.grants_access:
            raise InvariantViolation("cannot change the role of an inactive membership")
        self.role = role
        self.updated_at = now or _utcnow()

    def remove(self, *, now: datetime | None = None) -> None:
        """Revoke team access while keeping the row for audit history."""
        self.status = MembershipStatus.DEPROVISIONED
        self.updated_at = now or _utcnow()

    def reactivate(self, *, role: TeamRole | None = None, now: datetime | None = None) -> None:
        """Restore a removed team membership.

        A team is organisation-only and never a tenant boundary, so rejoining is
        routine. Reviving the existing row keeps the (team_id, user_id) unique
        constraint and the membership's history intact.
        """
        if role is not None:
            self.role = role
        self.status = MembershipStatus.ACTIVE
        self.updated_at = now or _utcnow()


__all__ = [
    "Organization",
    "OrganizationMembership",
    "OrganizationUnit",
    "Team",
    "TeamMembership",
]
