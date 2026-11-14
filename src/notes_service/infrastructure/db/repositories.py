"""SQLAlchemy implementations of the persistence ports.

Each repository maps between ORM rows and domain objects. Repositories never
commit; the :class:`SqlAlchemyUnitOfWork` owns transaction boundaries.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from notes_service.domain.errors import VersionConflict
from notes_service.domain.identity.entities import ExternalIdentity, User
from notes_service.domain.identity.value_objects import (
    Email,
    ExternalIdentityId,
    ProviderSubject,
    UserId,
    UserStatus,
)
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.permission import NotePermission, NotePermissionId
from notes_service.domain.notes.value_objects import (
    NoteContent,
    NoteId,
    NoteTitle,
    NoteVersion,
    PermissionLevel,
)
from notes_service.infrastructure.db.models import (
    ExternalIdentityRow,
    NotePermissionRow,
    NoteRow,
    UserRow,
)


def _as_uuid(value: object) -> uuid.UUID:
    """Coerce domain identifier subclasses to plain UUIDs for the driver."""
    return uuid.UUID(str(value))


def _user_to_domain(row: UserRow) -> User:
    return User(
        id=UserId(str(row.id)),
        email=Email(row.email),
        display_name=row.display_name,
        status=UserStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _permission_to_domain(row: NotePermissionRow) -> NotePermission:
    return NotePermission(
        id=NotePermissionId(str(row.id)),
        note_id=NoteId(str(row.note_id)),
        user_id=UserId(str(row.user_id)),
        level=PermissionLevel(row.permission),
        granted_by=UserId(str(row.granted_by)),
        created_at=row.created_at,
    )


def _note_to_domain(row: NoteRow, permissions: list[NotePermission]) -> Note:
    return Note(
        id=NoteId(str(row.id)),
        owner_id=UserId(str(row.owner_id)),
        title=NoteTitle(row.title),
        content=NoteContent(row.content),
        version=NoteVersion(row.version),
        created_at=row.created_at,
        updated_at=row.updated_at,
        permissions=permissions,
    )


class SqlAlchemyUserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, user: User) -> None:
        self._session.add(
            UserRow(
                id=_as_uuid(user.id),
                email=str(user.email),
                display_name=user.display_name,
                status=user.status.value,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
        )

    def get(self, user_id: UserId) -> User | None:
        row = self._session.get(UserRow, _as_uuid(user_id))
        return _user_to_domain(row) if row else None

    def get_by_email(self, email: Email) -> User | None:
        row = self._session.execute(
            select(UserRow).where(UserRow.email == str(email))
        ).scalar_one_or_none()
        return _user_to_domain(row) if row else None

    def list_all(self, *, limit: int, offset: int) -> list[User]:
        rows = (
            self._session.execute(
                select(UserRow)
                .order_by(UserRow.display_name, UserRow.id)
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return [_user_to_domain(row) for row in rows]

    def count(self) -> int:
        return int(self._session.execute(select(func.count()).select_from(UserRow)).scalar_one())


class SqlAlchemyExternalIdentityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, identity: ExternalIdentity) -> None:
        self._session.add(
            ExternalIdentityRow(
                id=_as_uuid(identity.id),
                user_id=_as_uuid(identity.user_id),
                provider=identity.provider,
                subject=identity.subject,
                created_at=identity.created_at,
            )
        )

    def get(self, identity_id: ExternalIdentityId) -> ExternalIdentity | None:
        row = self._session.get(ExternalIdentityRow, _as_uuid(identity_id))
        return self._row_to_domain(row) if row else None

    def find_by_provider_subject(
        self, provider_subject: ProviderSubject
    ) -> ExternalIdentity | None:
        row = self._session.execute(
            select(ExternalIdentityRow).where(
                ExternalIdentityRow.provider == provider_subject.provider,
                ExternalIdentityRow.subject == provider_subject.subject,
            )
        ).scalar_one_or_none()
        return self._row_to_domain(row) if row else None

    def list_for_user(self, user_id: UserId) -> list[ExternalIdentity]:
        rows = (
            self._session.execute(
                select(ExternalIdentityRow).where(ExternalIdentityRow.user_id == _as_uuid(user_id))
            )
            .scalars()
            .all()
        )
        return [self._row_to_domain(row) for row in rows]

    def list_by_provider(self, provider: str) -> list[ExternalIdentity]:
        rows = (
            self._session.execute(
                select(ExternalIdentityRow)
                .where(ExternalIdentityRow.provider == provider)
                .order_by(ExternalIdentityRow.created_at, ExternalIdentityRow.id)
            )
            .scalars()
            .all()
        )
        return [self._row_to_domain(row) for row in rows]

    @staticmethod
    def _row_to_domain(row: ExternalIdentityRow) -> ExternalIdentity:
        return ExternalIdentity(
            id=ExternalIdentityId(str(row.id)),
            user_id=UserId(str(row.user_id)),
            provider_subject=ProviderSubject(provider=row.provider, subject=row.subject),
            created_at=row.created_at,
        )


class SqlAlchemyNoteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, note: Note) -> None:
        self._session.add(
            NoteRow(
                id=_as_uuid(note.id),
                owner_id=_as_uuid(note.owner_id),
                title=str(note.title),
                content=str(note.content),
                version=int(note.version),
                created_at=note.created_at,
                updated_at=note.updated_at,
            )
        )
        self._session.flush()

    def get(self, note_id: NoteId) -> Note | None:
        row = self._session.get(NoteRow, _as_uuid(note_id))
        if row is None:
            return None
        permissions = [
            _permission_to_domain(p)
            for p in self._session.execute(
                select(NotePermissionRow).where(NotePermissionRow.note_id == row.id)
            )
            .scalars()
            .all()
        ]
        return _note_to_domain(row, permissions)

    def save(self, note: Note, *, expected_version: int | None = None) -> None:
        """Atomically update a note's scalar fields under optimistic concurrency.

        The compare-and-set is expressed as a single ``UPDATE ... WHERE
        version = :expected`` so that two concurrent writers cannot both
        succeed: the loser matches zero rows and receives a conflict.
        """
        values: dict[str, object] = {
            "title": str(note.title),
            "content": str(note.content),
            "version": int(note.version),
            "updated_at": note.updated_at,
        }
        stmt = update(NoteRow).where(NoteRow.id == _as_uuid(note.id))
        if expected_version is not None:
            stmt = stmt.where(NoteRow.version == expected_version)
        result = self._session.execute(stmt.values(**values))
        if expected_version is not None and cast(CursorResult[Any], result).rowcount != 1:
            current = self._session.execute(
                select(NoteRow.version).where(NoteRow.id == _as_uuid(note.id))
            ).scalar_one_or_none()
            raise VersionConflict(
                expected=int(expected_version),
                actual=int(current) if current is not None else int(note.version),
            )

    def delete(self, note: Note) -> None:
        row = self._session.get(NoteRow, _as_uuid(note.id))
        if row is not None:
            self._session.delete(row)

    def list_accessible(self, user_id: UserId, *, limit: int, offset: int, sort: str) -> list[Note]:
        uid = _as_uuid(user_id)
        # A note is accessible if the user owns it or holds any permission.
        # DISTINCT collapses the (owner + redundant grant) case so a note never
        # appears twice.
        accessible = (
            select(NoteRow.id)
            .outerjoin(NotePermissionRow, NotePermissionRow.note_id == NoteRow.id)
            .where((NoteRow.owner_id == uid) | (NotePermissionRow.user_id == uid))
            .distinct()
        )
        order = self._order_by(sort)
        rows = (
            self._session.execute(
                select(NoteRow)
                .where(NoteRow.id.in_(accessible))
                .order_by(*order)
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return [self._hydrate(row) for row in rows]

    def count_accessible(self, user_id: UserId) -> int:
        uid = _as_uuid(user_id)
        accessible = (
            select(NoteRow.id)
            .outerjoin(NotePermissionRow, NotePermissionRow.note_id == NoteRow.id)
            .where((NoteRow.owner_id == uid) | (NotePermissionRow.user_id == uid))
            .distinct()
        )
        return int(
            self._session.execute(
                select(func.count()).select_from(accessible.subquery())
            ).scalar_one()
        )

    def _hydrate(self, row: NoteRow) -> Note:
        permissions = [
            _permission_to_domain(p)
            for p in self._session.execute(
                select(NotePermissionRow).where(NotePermissionRow.note_id == row.id)
            )
            .scalars()
            .all()
        ]
        return _note_to_domain(row, permissions)

    @staticmethod
    def _order_by(sort: str) -> list[ColumnElement[Any]]:
        descending = sort.startswith("-")
        field = sort.lstrip("-")
        column = {
            "updated_at": NoteRow.updated_at,
            "created_at": NoteRow.created_at,
            "title": NoteRow.title,
        }.get(field, NoteRow.updated_at)
        ordered: ColumnElement[Any] = column.desc() if descending else column.asc()
        # Secondary key keeps pagination stable when the primary key ties.
        return [ordered, NoteRow.id.asc()]


class SqlAlchemyNotePermissionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, permission: NotePermission) -> None:
        existing = self.get(permission.note_id, permission.user_id)
        if existing is not None:
            self._session.execute(
                update(NotePermissionRow)
                .where(
                    NotePermissionRow.note_id == _as_uuid(permission.note_id),
                    NotePermissionRow.user_id == _as_uuid(permission.user_id),
                )
                .values(
                    permission=permission.level.value, granted_by=_as_uuid(permission.granted_by)
                )
            )
            return
        self._session.add(
            NotePermissionRow(
                id=_as_uuid(permission.id),
                note_id=_as_uuid(permission.note_id),
                user_id=_as_uuid(permission.user_id),
                permission=permission.level.value,
                granted_by=_as_uuid(permission.granted_by),
                created_at=permission.created_at,
            )
        )

    def get(self, note_id: NoteId, user_id: UserId) -> NotePermission | None:
        row = self._session.execute(
            select(NotePermissionRow).where(
                NotePermissionRow.note_id == _as_uuid(note_id),
                NotePermissionRow.user_id == _as_uuid(user_id),
            )
        ).scalar_one_or_none()
        return _permission_to_domain(row) if row else None

    def list_for_note(self, note_id: NoteId) -> list[NotePermission]:
        rows = (
            self._session.execute(
                select(NotePermissionRow)
                .where(NotePermissionRow.note_id == _as_uuid(note_id))
                .order_by(NotePermissionRow.created_at)
            )
            .scalars()
            .all()
        )
        return [_permission_to_domain(row) for row in rows]

    def remove(self, note_id: NoteId, user_id: UserId) -> bool:
        result = self._session.execute(
            sql_delete(NotePermissionRow).where(
                NotePermissionRow.note_id == _as_uuid(note_id),
                NotePermissionRow.user_id == _as_uuid(user_id),
            )
        )
        return cast(CursorResult[Any], result).rowcount > 0


__all__ = [
    "SqlAlchemyExternalIdentityRepository",
    "SqlAlchemyNotePermissionRepository",
    "SqlAlchemyNoteRepository",
    "SqlAlchemyUserRepository",
]
