"""SQLAlchemy adapters for the organization repository ports.

Kept in its own module so the notes adapters stay readable. Conventions match
:mod:`notes_service.infrastructure.db.repositories`: repositories never commit
and mutations of versioned aggregates are conditional compare-and-set updates.

Every read is scoped by organization id where the caller could otherwise reach
another tenant's rows; the ports make the scope a required argument so it cannot
be forgotten at a call site.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy import delete as sql_delete
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from notes_service.domain.errors import VersionConflict
from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.organization.entities import (
    Organization,
    OrganizationMembership,
    OrganizationUnit,
    Team,
    TeamMembership,
)
from notes_service.domain.organization.value_objects import (
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
from notes_service.infrastructure.db.models import (
    OrganizationMembershipRow,
    OrganizationRow,
    OrganizationUnitRow,
    TeamMembershipRow,
    TeamRow,
)


def _as_uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


def _maybe_uuid(value: object | None) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(str(value))


def _raise_conflict(
    session: Session, model: type[Any], row_id: uuid.UUID, expected: int, fallback: int
) -> None:
    """Raise ``VersionConflict`` describing the row's actual current version."""
    current = session.execute(select(model.version).where(model.id == row_id)).scalar_one_or_none()
    raise VersionConflict(
        expected=int(expected), actual=int(current) if current is not None else fallback
    )


# -- domain <-> row mappers ------------------------------------------------


def _org_to_domain(row: OrganizationRow) -> Organization:
    return Organization(
        id=OrganizationId(str(row.id)),
        slug=Slug(row.slug),
        name=OrganizationName(row.name),
        status=OrganizationStatus(row.status),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _unit_to_domain(row: OrganizationUnitRow) -> OrganizationUnit:
    return OrganizationUnit(
        id=OrganizationUnitId(str(row.id)),
        organization_id=OrganizationId(str(row.organization_id)),
        name=OrganizationName(row.name),
        slug=Slug(row.slug),
        unit_type=OrgUnitType(row.unit_type),
        parent_id=OrganizationUnitId(str(row.parent_id)) if row.parent_id else None,
        status=UnitStatus(row.status),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _team_to_domain(row: TeamRow) -> Team:
    return Team(
        id=TeamId(str(row.id)),
        organization_id=OrganizationId(str(row.organization_id)),
        unit_id=OrganizationUnitId(str(row.unit_id)),
        name=OrganizationName(row.name),
        slug=Slug(row.slug),
        parent_team_id=TeamId(str(row.parent_team_id)) if row.parent_team_id else None,
        status=TeamStatus(row.status),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _org_membership_to_domain(row: OrganizationMembershipRow) -> OrganizationMembership:
    return OrganizationMembership(
        id=OrganizationMembershipId(str(row.id)),
        organization_id=OrganizationId(str(row.organization_id)),
        user_id=str(row.user_id),
        role=OrganizationRole(row.role),
        status=MembershipStatus(row.status),
        manager_user_id=str(row.manager_user_id) if row.manager_user_id else None,
        title=row.title,
        started_at=row.started_at,
        ended_at=row.ended_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _team_membership_to_domain(row: TeamMembershipRow) -> TeamMembership:
    return TeamMembership(
        id=TeamMembershipId(str(row.id)),
        team_id=TeamId(str(row.team_id)),
        user_id=str(row.user_id),
        role=TeamRole(row.role),
        status=MembershipStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# -- repositories ----------------------------------------------------------


class SqlAlchemyOrganizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, organization: Organization) -> None:
        self._session.add(
            OrganizationRow(
                id=_as_uuid(organization.id),
                slug=str(organization.slug),
                name=str(organization.name),
                status=organization.status.value,
                version=organization.version,
                created_at=organization.created_at,
                updated_at=organization.updated_at,
            )
        )
        self._session.flush()

    def get(self, organization_id: OrganizationId) -> Organization | None:
        row = self._session.get(OrganizationRow, _as_uuid(organization_id))
        return _org_to_domain(row) if row else None

    def get_by_slug(self, slug: Slug) -> Organization | None:
        row = self._session.execute(
            select(OrganizationRow).where(OrganizationRow.slug == str(slug))
        ).scalar_one_or_none()
        return _org_to_domain(row) if row else None

    def save(self, organization: Organization, *, expected_version: int | None = None) -> None:
        stmt = (
            update(OrganizationRow)
            .where(OrganizationRow.id == _as_uuid(organization.id))
            .values(
                slug=str(organization.slug),
                name=str(organization.name),
                status=organization.status.value,
                version=organization.version,
                updated_at=organization.updated_at,
            )
        )
        if expected_version is not None:
            stmt = stmt.where(OrganizationRow.version == expected_version)
        result = self._session.execute(stmt)
        if expected_version is not None and cast(CursorResult[Any], result).rowcount != 1:
            _raise_conflict(
                self._session,
                OrganizationRow,
                _as_uuid(organization.id),
                expected_version,
                organization.version,
            )

    def slug_exists(self, slug: Slug) -> bool:
        return (
            self._session.execute(
                select(OrganizationRow.id).where(OrganizationRow.slug == str(slug))
            ).scalar_one_or_none()
            is not None
        )

    def list_for_user(self, user_id: UserId) -> list[Organization]:
        rows = (
            self._session.execute(
                select(OrganizationRow)
                .join(
                    OrganizationMembershipRow,
                    OrganizationMembershipRow.organization_id == OrganizationRow.id,
                )
                .where(
                    OrganizationMembershipRow.user_id == _as_uuid(user_id),
                    OrganizationMembershipRow.status == MembershipStatus.ACTIVE.value,
                )
                .order_by(func.lower(OrganizationRow.name), OrganizationRow.id)
            )
            .scalars()
            .all()
        )
        return [_org_to_domain(row) for row in rows]


class SqlAlchemyOrganizationUnitRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, unit: OrganizationUnit) -> None:
        self._session.add(
            OrganizationUnitRow(
                id=_as_uuid(unit.id),
                organization_id=_as_uuid(unit.organization_id),
                parent_id=_maybe_uuid(unit.parent_id),
                name=str(unit.name),
                slug=str(unit.slug),
                unit_type=unit.unit_type.value,
                status=unit.status.value,
                version=unit.version,
                created_at=unit.created_at,
                updated_at=unit.updated_at,
            )
        )
        self._session.flush()

    def get(self, unit_id: OrganizationUnitId) -> OrganizationUnit | None:
        row = self._session.get(OrganizationUnitRow, _as_uuid(unit_id))
        return _unit_to_domain(row) if row else None

    def save(self, unit: OrganizationUnit, *, expected_version: int | None = None) -> None:
        stmt = (
            update(OrganizationUnitRow)
            .where(OrganizationUnitRow.id == _as_uuid(unit.id))
            .values(
                parent_id=_maybe_uuid(unit.parent_id),
                name=str(unit.name),
                slug=str(unit.slug),
                unit_type=unit.unit_type.value,
                status=unit.status.value,
                version=unit.version,
                updated_at=unit.updated_at,
            )
        )
        if expected_version is not None:
            stmt = stmt.where(OrganizationUnitRow.version == expected_version)
        result = self._session.execute(stmt)
        if expected_version is not None and cast(CursorResult[Any], result).rowcount != 1:
            _raise_conflict(
                self._session,
                OrganizationUnitRow,
                _as_uuid(unit.id),
                expected_version,
                unit.version,
            )

    def delete(self, unit: OrganizationUnit) -> None:
        self._session.execute(
            sql_delete(OrganizationUnitRow).where(OrganizationUnitRow.id == _as_uuid(unit.id))
        )

    def list_for_organization(self, organization_id: OrganizationId) -> list[OrganizationUnit]:
        rows = (
            self._session.execute(
                select(OrganizationUnitRow)
                .where(OrganizationUnitRow.organization_id == _as_uuid(organization_id))
                .order_by(func.lower(OrganizationUnitRow.name), OrganizationUnitRow.id)
            )
            .scalars()
            .all()
        )
        return [_unit_to_domain(row) for row in rows]

    def slug_exists(
        self,
        organization_id: OrganizationId,
        slug: Slug,
        *,
        exclude_id: OrganizationUnitId | None = None,
    ) -> bool:
        stmt = select(OrganizationUnitRow.id).where(
            OrganizationUnitRow.organization_id == _as_uuid(organization_id),
            OrganizationUnitRow.slug == str(slug),
        )
        if exclude_id is not None:
            stmt = stmt.where(OrganizationUnitRow.id != _as_uuid(exclude_id))
        return self._session.execute(stmt).scalar_one_or_none() is not None

    def ancestor_path(self, unit_id: OrganizationUnitId) -> list[OrganizationUnitId]:
        """Walk ``parent_id`` upwards, root first.

        Iterative rather than recursive so it works identically on SQLite and
        PostgreSQL. The loop is bounded by :data:`MAX_HIERARCHY_DEPTH` to make a
        corrupted (cyclic) row set terminate instead of hanging.
        """
        path: list[OrganizationUnitId] = []
        seen: set[uuid.UUID] = set()
        current = self._session.get(OrganizationUnitRow, _as_uuid(unit_id))
        guard = 0
        while current is not None and current.parent_id is not None:
            parent_uuid = current.parent_id
            if parent_uuid in seen:  # defensive: never loop forever on bad data
                break
            seen.add(parent_uuid)
            path.append(OrganizationUnitId(str(parent_uuid)))
            current = self._session.get(OrganizationUnitRow, parent_uuid)
            guard += 1
            if guard > 32:
                break
        path.reverse()
        return path

    def has_children(self, unit_id: OrganizationUnitId) -> bool:
        return (
            self._session.execute(
                select(OrganizationUnitRow.id).where(
                    OrganizationUnitRow.parent_id == _as_uuid(unit_id)
                )
            ).first()
            is not None
        )


class SqlAlchemyTeamRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, team: Team) -> None:
        self._session.add(
            TeamRow(
                id=_as_uuid(team.id),
                organization_id=_as_uuid(team.organization_id),
                unit_id=_as_uuid(team.unit_id),
                parent_team_id=_maybe_uuid(team.parent_team_id),
                name=str(team.name),
                slug=str(team.slug),
                status=team.status.value,
                version=team.version,
                created_at=team.created_at,
                updated_at=team.updated_at,
            )
        )
        self._session.flush()

    def get(self, team_id: TeamId) -> Team | None:
        row = self._session.get(TeamRow, _as_uuid(team_id))
        return _team_to_domain(row) if row else None

    def save(self, team: Team, *, expected_version: int | None = None) -> None:
        stmt = (
            update(TeamRow)
            .where(TeamRow.id == _as_uuid(team.id))
            .values(
                unit_id=_as_uuid(team.unit_id),
                parent_team_id=_maybe_uuid(team.parent_team_id),
                name=str(team.name),
                slug=str(team.slug),
                status=team.status.value,
                version=team.version,
                updated_at=team.updated_at,
            )
        )
        if expected_version is not None:
            stmt = stmt.where(TeamRow.version == expected_version)
        result = self._session.execute(stmt)
        if expected_version is not None and cast(CursorResult[Any], result).rowcount != 1:
            _raise_conflict(
                self._session, TeamRow, _as_uuid(team.id), expected_version, team.version
            )

    def delete(self, team: Team) -> None:
        self._session.execute(sql_delete(TeamRow).where(TeamRow.id == _as_uuid(team.id)))

    def list_for_organization(self, organization_id: OrganizationId) -> list[Team]:
        rows = (
            self._session.execute(
                select(TeamRow)
                .where(TeamRow.organization_id == _as_uuid(organization_id))
                .order_by(func.lower(TeamRow.name), TeamRow.id)
            )
            .scalars()
            .all()
        )
        return [_team_to_domain(row) for row in rows]

    def list_for_unit(self, unit_id: OrganizationUnitId) -> list[Team]:
        rows = (
            self._session.execute(
                select(TeamRow)
                .where(TeamRow.unit_id == _as_uuid(unit_id))
                .order_by(func.lower(TeamRow.name), TeamRow.id)
            )
            .scalars()
            .all()
        )
        return [_team_to_domain(row) for row in rows]

    def slug_exists(
        self, unit_id: OrganizationUnitId, slug: Slug, *, exclude_id: TeamId | None = None
    ) -> bool:
        stmt = select(TeamRow.id).where(
            TeamRow.unit_id == _as_uuid(unit_id), TeamRow.slug == str(slug)
        )
        if exclude_id is not None:
            stmt = stmt.where(TeamRow.id != _as_uuid(exclude_id))
        return self._session.execute(stmt).scalar_one_or_none() is not None

    def ancestor_path(self, team_id: TeamId) -> list[TeamId]:
        path: list[TeamId] = []
        seen: set[uuid.UUID] = set()
        current = self._session.get(TeamRow, _as_uuid(team_id))
        guard = 0
        while current is not None and current.parent_team_id is not None:
            parent_uuid = current.parent_team_id
            if parent_uuid in seen:
                break
            seen.add(parent_uuid)
            path.append(TeamId(str(parent_uuid)))
            current = self._session.get(TeamRow, parent_uuid)
            guard += 1
            if guard > 32:
                break
        path.reverse()
        return path


class SqlAlchemyOrganizationMembershipRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, membership: OrganizationMembership) -> None:
        self._session.add(
            OrganizationMembershipRow(
                id=_as_uuid(membership.id),
                organization_id=_as_uuid(membership.organization_id),
                user_id=_as_uuid(membership.user_id),
                role=membership.role.value,
                status=membership.status.value,
                manager_user_id=_maybe_uuid(membership.manager_user_id),
                title=membership.title,
                started_at=membership.started_at,
                ended_at=membership.ended_at,
                created_at=membership.created_at,
                updated_at=membership.updated_at,
            )
        )
        self._session.flush()

    def get(self, membership_id: OrganizationMembershipId) -> OrganizationMembership | None:
        row = self._session.get(OrganizationMembershipRow, _as_uuid(membership_id))
        return _org_membership_to_domain(row) if row else None

    def get_for_user(
        self, organization_id: OrganizationId, user_id: UserId
    ) -> OrganizationMembership | None:
        row = self._session.execute(
            select(OrganizationMembershipRow).where(
                OrganizationMembershipRow.organization_id == _as_uuid(organization_id),
                OrganizationMembershipRow.user_id == _as_uuid(user_id),
            )
        ).scalar_one_or_none()
        return _org_membership_to_domain(row) if row else None

    def save(self, membership: OrganizationMembership) -> None:
        self._session.execute(
            update(OrganizationMembershipRow)
            .where(OrganizationMembershipRow.id == _as_uuid(membership.id))
            .values(
                role=membership.role.value,
                status=membership.status.value,
                manager_user_id=_maybe_uuid(membership.manager_user_id),
                title=membership.title,
                started_at=membership.started_at,
                ended_at=membership.ended_at,
                updated_at=membership.updated_at,
            )
        )

    @staticmethod
    def _roster_predicate(organization_id: OrganizationId) -> list[ColumnElement[bool]]:
        # Offboarding is a soft delete (status -> DEPROVISIONED): the row is kept
        # for audit history, so the roster has to exclude it explicitly or an
        # offboarded person keeps appearing as a current member. Only the
        # terminal state is filtered out, not every non-active one: suspension is
        # a reversible pause and the reactivate endpoint needs the membership id,
        # so a suspended member must stay on the roster to be actionable.
        #
        # The listing and the count share this predicate deliberately: a count
        # that selected a different set would report a total no page can reach,
        # which is the same class of defect as a total derived from len(page).
        return [
            OrganizationMembershipRow.organization_id == _as_uuid(organization_id),
            OrganizationMembershipRow.status != MembershipStatus.DEPROVISIONED.value,
        ]

    def list_for_organization(
        self, organization_id: OrganizationId, *, limit: int | None = None, offset: int = 0
    ) -> list[OrganizationMembership]:
        # ``id`` breaks ties so the OFFSET window is stable: memberships invited
        # in one request burst share ``created_at``, and an order that is not
        # total can repeat a row while skipping another as the window shifts.
        statement = (
            select(OrganizationMembershipRow)
            .where(*self._roster_predicate(organization_id))
            .order_by(OrganizationMembershipRow.created_at, OrganizationMembershipRow.id)
        )
        if limit is not None:
            statement = statement.limit(limit).offset(offset)
        rows = self._session.execute(statement).scalars().all()
        return [_org_membership_to_domain(row) for row in rows]

    def count_for_organization(self, organization_id: OrganizationId) -> int:
        # An aggregate reads no rows, so the total costs the same at any tenant
        # size, and it is scoped by organization for the same reason the listing
        # is: an unscoped count would disclose another tenant's member volume.
        return int(
            self._session.execute(
                select(func.count())
                .select_from(OrganizationMembershipRow)
                .where(*self._roster_predicate(organization_id))
            ).scalar_one()
        )

    def list_for_user(self, user_id: UserId) -> list[OrganizationMembership]:
        rows = (
            self._session.execute(
                select(OrganizationMembershipRow)
                .where(OrganizationMembershipRow.user_id == _as_uuid(user_id))
                .order_by(OrganizationMembershipRow.created_at, OrganizationMembershipRow.id)
            )
            .scalars()
            .all()
        )
        return [_org_membership_to_domain(row) for row in rows]

    def count_active_admins(self, organization_id: OrganizationId) -> int:
        admin_roles = (
            OrganizationRole.ORG_OWNER.value,
            OrganizationRole.ORG_ADMIN.value,
        )
        return int(
            self._session.execute(
                select(func.count())
                .select_from(OrganizationMembershipRow)
                .where(
                    OrganizationMembershipRow.organization_id == _as_uuid(organization_id),
                    OrganizationMembershipRow.status == MembershipStatus.ACTIVE.value,
                    OrganizationMembershipRow.role.in_(admin_roles),
                )
            ).scalar_one()
        )


class SqlAlchemyTeamMembershipRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, membership: TeamMembership) -> None:
        self._session.add(
            TeamMembershipRow(
                id=_as_uuid(membership.id),
                team_id=_as_uuid(membership.team_id),
                user_id=_as_uuid(membership.user_id),
                role=membership.role.value,
                status=membership.status.value,
                created_at=membership.created_at,
                updated_at=membership.updated_at,
            )
        )
        self._session.flush()

    def get(self, membership_id: TeamMembershipId) -> TeamMembership | None:
        row = self._session.get(TeamMembershipRow, _as_uuid(membership_id))
        return _team_membership_to_domain(row) if row else None

    def get_for_user(self, team_id: TeamId, user_id: UserId) -> TeamMembership | None:
        row = self._session.execute(
            select(TeamMembershipRow).where(
                TeamMembershipRow.team_id == _as_uuid(team_id),
                TeamMembershipRow.user_id == _as_uuid(user_id),
            )
        ).scalar_one_or_none()
        return _team_membership_to_domain(row) if row else None

    def save(self, membership: TeamMembership) -> None:
        self._session.execute(
            update(TeamMembershipRow)
            .where(TeamMembershipRow.id == _as_uuid(membership.id))
            .values(
                role=membership.role.value,
                status=membership.status.value,
                updated_at=membership.updated_at,
            )
        )

    def list_for_team(self, team_id: TeamId) -> list[TeamMembership]:
        # Membership removal is a soft delete (status -> DEPROVISIONED), so the
        # "members of this team" query must exclude non-access-granting rows or a
        # removed member keeps appearing in the roster.
        rows = (
            self._session.execute(
                select(TeamMembershipRow)
                .where(
                    TeamMembershipRow.team_id == _as_uuid(team_id),
                    TeamMembershipRow.status == MembershipStatus.ACTIVE.value,
                )
                .order_by(TeamMembershipRow.created_at, TeamMembershipRow.id)
            )
            .scalars()
            .all()
        )
        return [_team_membership_to_domain(row) for row in rows]

    def list_for_user(self, user_id: UserId) -> list[TeamMembership]:
        rows = (
            self._session.execute(
                select(TeamMembershipRow)
                .where(TeamMembershipRow.user_id == _as_uuid(user_id))
                .order_by(TeamMembershipRow.created_at, TeamMembershipRow.id)
            )
            .scalars()
            .all()
        )
        return [_team_membership_to_domain(row) for row in rows]


__all__ = [
    "SqlAlchemyOrganizationMembershipRepository",
    "SqlAlchemyOrganizationRepository",
    "SqlAlchemyOrganizationUnitRepository",
    "SqlAlchemyTeamMembershipRepository",
    "SqlAlchemyTeamRepository",
]
