"""SQLAlchemy Unit of Work.

A single :class:`Session` is shared by every repository for the duration of one
use case, so all writes succeed or fail together. The UoW never commits on
``__exit__``: the use case must call :meth:`commit` explicitly. This makes
transaction boundaries visible in the application layer and guarantees a
rollback if anything raises.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.ports.repositories import (
    ExternalIdentityRepository,
    NotePermissionRepository,
    NoteRepository,
    UnitOfWork,
    UserRepository,
)
from notes_service.infrastructure.db.repositories import (
    SqlAlchemyExternalIdentityRepository,
    SqlAlchemyNotePermissionRepository,
    SqlAlchemyNoteRepository,
    SqlAlchemyUserRepository,
)


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Owns a session/transaction and exposes the repository adapters.

    Declares conformance to the :class:`UnitOfWork` port so the composition root
    can hand it to use cases without a cast.
    """

    users: UserRepository
    external_identities: ExternalIdentityRepository
    notes: NoteRepository
    permissions: NotePermissionRepository

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside of a context manager")
        return self._session

    def __enter__(self) -> Self:
        self._session = self._session_factory()
        self.users = SqlAlchemyUserRepository(self._session)
        self.external_identities = SqlAlchemyExternalIdentityRepository(self._session)
        self.notes = SqlAlchemyNoteRepository(self._session)
        self.permissions = SqlAlchemyNotePermissionRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._session is not None
        try:
            if exc_type is not None:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
