"""SQLAlchemy implementations of the persistence ports.

Each repository maps between ORM rows and domain objects. Repositories never
commit; the :class:`SqlAlchemyUnitOfWork` owns transaction boundaries.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from notes_service.domain.errors import VersionConflict
from notes_service.domain.identity.entities import ExternalIdentity, User
from notes_service.domain.identity.session import LoginTransaction, UserSession
from notes_service.domain.identity.value_objects import (
    Email,
    ExternalIdentityId,
    LoginTransactionId,
    ProviderSubject,
    SessionId,
    UserId,
    UserStatus,
)
from notes_service.domain.notes.folder import Folder
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.permission import NotePermission, NotePermissionId
from notes_service.domain.notes.value_objects import (
    FolderId,
    FolderName,
    NoteContent,
    NoteId,
    NoteTitle,
    NoteVersion,
    PermissionLevel,
)
from notes_service.infrastructure.db.models import (
    ExternalIdentityRow,
    FolderRow,
    LoginTransactionRow,
    NotePermissionRow,
    NoteRow,
    NoteStarRow,
    UserRow,
    UserSessionRow,
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
        folder_id=FolderId(str(row.folder_id)) if row.folder_id is not None else None,
    )


def _folder_to_domain(row: FolderRow) -> Folder:
    return Folder(
        id=FolderId(str(row.id)),
        owner_id=UserId(str(row.owner_id)),
        name=FolderName(row.name),
        created_at=row.created_at,
        updated_at=row.updated_at,
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
                folder_id=_as_uuid(note.folder_id) if note.folder_id is not None else None,
                created_at=note.created_at,
                updated_at=note.updated_at,
            )
        )
        self._session.flush()

    def get(self, note_id: NoteId) -> Note | None:
        row = self._session.get(NoteRow, _as_uuid(note_id))
        if row is None:
            return None
        return self._hydrate(row)

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
            "folder_id": _as_uuid(note.folder_id) if note.folder_id is not None else None,
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

    def save_placement(self, note: Note) -> None:
        """Persist only folder placement + ``updated_at``; never touch version.

        Placement is not a content revision, so this write is deliberately
        version-free: a move must never make a concurrent content edit's version
        check fail, and vice versa.
        """
        self._session.execute(
            update(NoteRow)
            .where(NoteRow.id == _as_uuid(note.id))
            .values(
                folder_id=_as_uuid(note.folder_id) if note.folder_id is not None else None,
                updated_at=note.updated_at,
            )
        )

    def delete(self, note: Note) -> None:
        row = self._session.get(NoteRow, _as_uuid(note.id))
        if row is not None:
            self._session.delete(row)

    def list_accessible(
        self,
        user_id: UserId,
        *,
        limit: int,
        offset: int,
        sort: str,
        query: str | None = None,
        view: str = "all",
        folder_id: FolderId | None = None,
        starred_only: bool = False,
    ) -> list[Note]:
        uid = _as_uuid(user_id)
        # A note is accessible if the user owns it or holds any permission.
        # DISTINCT collapses the (owner + redundant grant) case so a note never
        # appears twice. The permission join is used *only* to widen visibility
        # for the caller; it is scoped to the caller's own user id, so another
        # user's grants can never surface a note here.
        accessible = (
            select(NoteRow.id)
            .outerjoin(NotePermissionRow, NotePermissionRow.note_id == NoteRow.id)
            .where((NoteRow.owner_id == uid) | (NotePermissionRow.user_id == uid))
            .distinct()
        )
        stmt = select(NoteRow).where(NoteRow.id.in_(accessible))
        stmt = self._apply_filters(
            stmt, uid=uid, query=query, view=view, folder_id=folder_id, starred_only=starred_only
        )
        rows = (
            self._session.execute(stmt.order_by(*self._order_by(sort)).limit(limit).offset(offset))
            .scalars()
            .all()
        )
        notes = [self._hydrate(row) for row in rows]
        self._annotate_stars(notes, uid)
        return notes

    def count_accessible(
        self,
        user_id: UserId,
        *,
        query: str | None = None,
        view: str = "all",
        folder_id: FolderId | None = None,
        starred_only: bool = False,
    ) -> int:
        uid = _as_uuid(user_id)
        accessible = (
            select(NoteRow.id)
            .outerjoin(NotePermissionRow, NotePermissionRow.note_id == NoteRow.id)
            .where((NoteRow.owner_id == uid) | (NotePermissionRow.user_id == uid))
            .distinct()
        )
        # ``view``/``query``/``folder``/``starred`` filters must apply to the
        # *outer* query over NoteRow, so build the filtered note query and count
        # its rows. Counting must use the same filters as listing, or pagination
        # metadata would disagree with the page contents.
        filtered = select(NoteRow).where(NoteRow.id.in_(accessible))
        filtered = self._apply_filters(
            filtered,
            uid=uid,
            query=query,
            view=view,
            folder_id=folder_id,
            starred_only=starred_only,
        )
        count_stmt = select(func.count()).select_from(filtered.subquery())
        return int(self._session.execute(count_stmt).scalar_one())

    def _apply_filters(
        self,
        stmt: Any,
        *,
        uid: uuid.UUID,
        query: str | None,
        view: str,
        folder_id: FolderId | None,
        starred_only: bool,
    ) -> Any:
        if view == "owned":
            stmt = stmt.where(NoteRow.owner_id == uid)
        elif view == "shared":
            stmt = stmt.where(NoteRow.owner_id != uid)
        if folder_id is not None:
            # Folder membership is meaningful only for the owner's own notes.
            stmt = stmt.where(NoteRow.owner_id == uid, NoteRow.folder_id == _as_uuid(folder_id))
        if starred_only:
            starred = select(NoteStarRow.note_id).where(NoteStarRow.user_id == uid)
            stmt = stmt.where(NoteRow.id.in_(starred))
        if query:
            # Case-insensitive substring match on title OR content. SQLite's LIKE
            # is case-insensitive for ASCII by default; lower() makes it explicit
            # and portable to PostgreSQL.
            needle = f"%{query.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(NoteRow.title).like(needle),
                    func.lower(NoteRow.content).like(needle),
                )
            )
        return stmt

    def _annotate_stars(self, notes: list[Note], uid: uuid.UUID) -> None:
        """Fill ``is_starred`` for the caller on the given notes, in one query."""
        if not notes:
            return
        ids = [_as_uuid(note.id) for note in notes]
        starred_ids = {
            str(row)
            for row in self._session.execute(
                select(NoteStarRow.note_id).where(
                    NoteStarRow.user_id == uid, NoteStarRow.note_id.in_(ids)
                )
            )
            .scalars()
            .all()
        }
        for note in notes:
            note.is_starred = str(note.id) in starred_ids

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


class SqlAlchemyFolderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, folder: Folder) -> None:
        self._session.add(
            FolderRow(
                id=_as_uuid(folder.id),
                owner_id=_as_uuid(folder.owner_id),
                name=str(folder.name),
                created_at=folder.created_at,
                updated_at=folder.updated_at,
            )
        )
        self._session.flush()

    def get(self, folder_id: FolderId) -> Folder | None:
        row = self._session.get(FolderRow, _as_uuid(folder_id))
        return _folder_to_domain(row) if row else None

    def save(self, folder: Folder) -> None:
        self._session.execute(
            update(FolderRow)
            .where(FolderRow.id == _as_uuid(folder.id))
            .values(name=str(folder.name), updated_at=folder.updated_at)
        )

    def delete(self, folder: Folder) -> None:
        row = self._session.get(FolderRow, _as_uuid(folder.id))
        if row is not None:
            self._session.delete(row)

    def list_for_owner(self, owner_id: UserId) -> list[Folder]:
        rows = (
            self._session.execute(
                select(FolderRow)
                .where(FolderRow.owner_id == _as_uuid(owner_id))
                .order_by(func.lower(FolderRow.name), FolderRow.id)
            )
            .scalars()
            .all()
        )
        return [_folder_to_domain(row) for row in rows]

    def name_exists(self, owner_id: UserId, name: str) -> bool:
        return (
            self._session.execute(
                select(FolderRow.id).where(
                    FolderRow.owner_id == _as_uuid(owner_id),
                    func.lower(FolderRow.name) == name.lower(),
                )
            ).scalar_one_or_none()
            is not None
        )


class SqlAlchemyNoteStarRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, note_id: NoteId, user_id: UserId) -> None:
        if self.exists(note_id, user_id):
            return
        self._session.add(NoteStarRow(note_id=_as_uuid(note_id), user_id=_as_uuid(user_id)))

    def remove(self, note_id: NoteId, user_id: UserId) -> bool:
        result = self._session.execute(
            sql_delete(NoteStarRow).where(
                NoteStarRow.note_id == _as_uuid(note_id),
                NoteStarRow.user_id == _as_uuid(user_id),
            )
        )
        return cast(CursorResult[Any], result).rowcount > 0

    def exists(self, note_id: NoteId, user_id: UserId) -> bool:
        return (
            self._session.execute(
                select(NoteStarRow.note_id).where(
                    NoteStarRow.note_id == _as_uuid(note_id),
                    NoteStarRow.user_id == _as_uuid(user_id),
                )
            ).scalar_one_or_none()
            is not None
        )


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


def _login_transaction_to_domain(row: LoginTransactionRow) -> LoginTransaction:
    return LoginTransaction(
        id=LoginTransactionId(str(row.id)),
        state=row.state,
        nonce=row.nonce,
        code_verifier=row.code_verifier,
        redirect_uri=row.redirect_uri,
        created_at=row.created_at,
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
    )


class SqlAlchemyLoginTransactionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, transaction: LoginTransaction) -> None:
        self._session.add(
            LoginTransactionRow(
                id=_as_uuid(transaction.id),
                state=transaction.state,
                nonce=transaction.nonce,
                code_verifier=transaction.code_verifier,
                redirect_uri=transaction.redirect_uri,
                created_at=transaction.created_at,
                expires_at=transaction.expires_at,
                consumed_at=transaction.consumed_at,
            )
        )

    def get_by_state(self, state: str) -> LoginTransaction | None:
        row = self._session.execute(
            select(LoginTransactionRow).where(LoginTransactionRow.state == state)
        ).scalar_one_or_none()
        return _login_transaction_to_domain(row) if row else None

    def save(self, transaction: LoginTransaction) -> None:
        row = self._session.get(LoginTransactionRow, _as_uuid(transaction.id))
        if row is None:
            self.add(transaction)
            return
        row.consumed_at = transaction.consumed_at

    def delete(self, transaction: LoginTransaction) -> None:
        self._session.execute(
            sql_delete(LoginTransactionRow).where(
                LoginTransactionRow.id == _as_uuid(transaction.id)
            )
        )

    def purge_expired(self, *, before: datetime) -> int:
        result = self._session.execute(
            sql_delete(LoginTransactionRow).where(LoginTransactionRow.expires_at < before)
        )
        return cast(CursorResult[Any], result).rowcount


def _session_to_domain(row: UserSessionRow) -> UserSession:
    return UserSession(
        id=SessionId(str(row.id)),
        user_id=UserId(str(row.user_id)),
        provider=row.provider,
        subject=row.subject,
        token_hash=row.token_hash,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        idle_expires_at=row.idle_expires_at,
        absolute_expires_at=row.absolute_expires_at,
        revoked_at=row.revoked_at,
    )


class SqlAlchemyUserSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, session: UserSession) -> None:
        self._session.add(
            UserSessionRow(
                id=_as_uuid(session.id),
                user_id=_as_uuid(session.user_id),
                provider=session.provider,
                subject=session.subject,
                token_hash=session.token_hash,
                created_at=session.created_at,
                last_seen_at=session.last_seen_at,
                idle_expires_at=session.idle_expires_at,
                absolute_expires_at=session.absolute_expires_at,
                revoked_at=session.revoked_at,
            )
        )

    def get_by_token_hash(self, token_hash: str) -> UserSession | None:
        row = self._session.execute(
            select(UserSessionRow).where(UserSessionRow.token_hash == token_hash)
        ).scalar_one_or_none()
        return _session_to_domain(row) if row else None

    def save(self, session: UserSession) -> None:
        row = self._session.get(UserSessionRow, _as_uuid(session.id))
        if row is None:
            self.add(session)
            return
        row.last_seen_at = session.last_seen_at
        row.idle_expires_at = session.idle_expires_at
        row.revoked_at = session.revoked_at

    def list_active_for_user(self, user_id: UserId, *, now: datetime) -> list[UserSession]:
        rows = (
            self._session.execute(
                select(UserSessionRow).where(
                    UserSessionRow.user_id == _as_uuid(user_id),
                    UserSessionRow.revoked_at.is_(None),
                    UserSessionRow.idle_expires_at > now,
                    UserSessionRow.absolute_expires_at > now,
                )
            )
            .scalars()
            .all()
        )
        return [_session_to_domain(row) for row in rows]

    def revoke_all_for_user(self, user_id: UserId, *, now: datetime) -> int:
        result = self._session.execute(
            update(UserSessionRow)
            .where(
                UserSessionRow.user_id == _as_uuid(user_id),
                UserSessionRow.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return cast(CursorResult[Any], result).rowcount


__all__ = [
    "SqlAlchemyExternalIdentityRepository",
    "SqlAlchemyFolderRepository",
    "SqlAlchemyLoginTransactionRepository",
    "SqlAlchemyNotePermissionRepository",
    "SqlAlchemyNoteRepository",
    "SqlAlchemyNoteStarRepository",
    "SqlAlchemyUserRepository",
    "SqlAlchemyUserSessionRepository",
]
