"""Repository ports for the organization bounded context.

These ports follow the same contract as the notes ports: repositories never
commit, and every listing is explicitly scoped by organization id. There is
deliberately **no** "list all organizations" method reachable without a
membership — the application layer resolves the caller's memberships first, so
tenant isolation is enforced by the port surface itself rather than by
remembering to add a ``WHERE`` clause.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.organization.entities import (
    Organization,
    OrganizationMembership,
    OrganizationUnit,
    Team,
    TeamMembership,
)
from notes_service.domain.organization.value_objects import (
    OrganizationId,
    OrganizationMembershipId,
    OrganizationUnitId,
    Slug,
    TeamId,
    TeamMembershipId,
)


@runtime_checkable
class OrganizationRepository(Protocol):
    """Persistence port for :class:`Organization`."""

    def add(self, organization: Organization) -> None: ...

    def get(self, organization_id: OrganizationId) -> Organization | None: ...

    def get_by_slug(self, slug: Slug) -> Organization | None: ...

    def save(self, organization: Organization, *, expected_version: int | None = None) -> None:
        """Persist changes; raise ``VersionConflict`` when ``expected_version``
        no longer matches."""
        ...

    def slug_exists(self, slug: Slug) -> bool: ...

    def list_for_user(self, user_id: UserId) -> list[Organization]:
        """Organizations the user is an *active* member of."""
        ...


@runtime_checkable
class OrganizationUnitRepository(Protocol):
    """Persistence port for :class:`OrganizationUnit`."""

    def add(self, unit: OrganizationUnit) -> None: ...

    def get(self, unit_id: OrganizationUnitId) -> OrganizationUnit | None: ...

    def save(self, unit: OrganizationUnit, *, expected_version: int | None = None) -> None: ...

    def delete(self, unit: OrganizationUnit) -> None: ...

    def list_for_organization(self, organization_id: OrganizationId) -> list[OrganizationUnit]: ...

    def slug_exists(
        self,
        organization_id: OrganizationId,
        slug: Slug,
        *,
        exclude_id: OrganizationUnitId | None = None,
    ) -> bool: ...

    def ancestor_path(self, unit_id: OrganizationUnitId) -> list[OrganizationUnitId]:
        """Ancestor ids of ``unit_id``, root first, excluding the unit itself."""
        ...

    def has_children(self, unit_id: OrganizationUnitId) -> bool: ...


@runtime_checkable
class TeamRepository(Protocol):
    """Persistence port for :class:`Team`."""

    def add(self, team: Team) -> None: ...

    def get(self, team_id: TeamId) -> Team | None: ...

    def save(self, team: Team, *, expected_version: int | None = None) -> None: ...

    def delete(self, team: Team) -> None: ...

    def list_for_organization(self, organization_id: OrganizationId) -> list[Team]: ...

    def list_for_unit(self, unit_id: OrganizationUnitId) -> list[Team]: ...

    def slug_exists(
        self, unit_id: OrganizationUnitId, slug: Slug, *, exclude_id: TeamId | None = None
    ) -> bool: ...

    def ancestor_path(self, team_id: TeamId) -> list[TeamId]:
        """Ancestor team ids of ``team_id``, root first, excluding the team."""
        ...


@runtime_checkable
class OrganizationMembershipRepository(Protocol):
    """Persistence port for organization memberships."""

    def add(self, membership: OrganizationMembership) -> None: ...

    def get(self, membership_id: OrganizationMembershipId) -> OrganizationMembership | None: ...

    def get_for_user(
        self, organization_id: OrganizationId, user_id: UserId
    ) -> OrganizationMembership | None: ...

    def save(self, membership: OrganizationMembership) -> None: ...

    def list_for_organization(
        self, organization_id: OrganizationId, *, limit: int | None = None, offset: int = 0
    ) -> list[OrganizationMembership]:
        """A page of the organization's roster, oldest first.

        Excludes ``DEPROVISIONED`` memberships: offboarding is a soft delete, so
        the row outlives the person's standing and would otherwise be listed as
        a current member. Keeps ``SUSPENDED`` ones, because reactivation is a
        legitimate next action and it needs the membership id.

        ``limit=None`` means the whole roster, which is why the caller that faces
        a client must pass one: there is no per-organization member cap, so an
        unpaged read grows without bound with the tenant. The order is
        ``(created_at, id)`` — an ``OFFSET`` window needs a **total** order, or a
        row can repeat while another is skipped as the window shifts, and
        memberships invited in one burst share ``created_at``.
        """
        ...

    def count_for_organization(self, organization_id: OrganizationId) -> int:
        """The roster's exact size, under the same predicate as the listing.

        Separate from :meth:`list_for_organization` so a page can report the
        set's real size. A total derived from ``len(page)`` cannot distinguish a
        complete roster from the first page of a larger one, which is exactly
        the judgement a paged consumer has to make.
        """
        ...

    def list_for_user(self, user_id: UserId) -> list[OrganizationMembership]: ...

    def count_active_admins(self, organization_id: OrganizationId) -> int:
        """Active members holding an administrative role.

        Used to refuse the last-admin removal that would strand an organization.
        """
        ...


@runtime_checkable
class TeamMembershipRepository(Protocol):
    """Persistence port for team memberships."""

    def add(self, membership: TeamMembership) -> None: ...

    def get(self, membership_id: TeamMembershipId) -> TeamMembership | None: ...

    def get_for_user(self, team_id: TeamId, user_id: UserId) -> TeamMembership | None: ...

    def save(self, membership: TeamMembership) -> None: ...

    def list_for_team(self, team_id: TeamId) -> list[TeamMembership]: ...

    def list_for_user(self, user_id: UserId) -> list[TeamMembership]: ...


__all__ = [
    "OrganizationMembershipRepository",
    "OrganizationRepository",
    "OrganizationUnitRepository",
    "TeamMembershipRepository",
    "TeamRepository",
]
