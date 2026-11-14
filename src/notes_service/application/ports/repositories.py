"""Repository and Unit-of-Work ports.

Repositories expose collection-like access to aggregates. They never commit;
transaction boundaries are owned exclusively by the :class:`UnitOfWork`. This
keeps atomicity decisions in the application layer where the use case is known.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from notes_service.domain.identity.entities import ExternalIdentity, User
from notes_service.domain.identity.value_objects import (
    Email,
    ExternalIdentityId,
    ProviderSubject,
    UserId,
)
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.permission import NotePermission
from notes_service.domain.notes.value_objects import NoteId


@runtime_checkable
class UserRepository(Protocol):
    """Persistence port for :class:`User` aggregates."""

    def add(self, user: User) -> None: ...

    def get(self, user_id: UserId) -> User | None: ...

    def get_by_email(self, email: Email) -> User | None: ...

    def list_all(self, *, limit: int, offset: int) -> list[User]: ...

    def count(self) -> int: ...


@runtime_checkable
class ExternalIdentityRepository(Protocol):
    """Persistence port linking external IdP subjects to local users."""

    def add(self, identity: ExternalIdentity) -> None: ...

    def get(self, identity_id: ExternalIdentityId) -> ExternalIdentity | None: ...

    def find_by_provider_subject(
        self, provider_subject: ProviderSubject
    ) -> ExternalIdentity | None: ...

    def list_for_user(self, user_id: UserId) -> list[ExternalIdentity]: ...

    def list_by_provider(self, provider: str) -> list[ExternalIdentity]: ...


@runtime_checkable
class NoteRepository(Protocol):
    """Persistence port for the :class:`Note` aggregate.

    ``add`` and ``get`` operate on the whole aggregate (note plus its permission
    grants) because the aggregate is the consistency boundary. ``save`` updates
    only the note's own fields under optimistic concurrency control; permission
    rows are written through :class:`NotePermissionRepository`.
    """

    def add(self, note: Note) -> None: ...

    def get(self, note_id: NoteId) -> Note | None: ...

    def save(self, note: Note, *, expected_version: int | None = None) -> None:
        """Persist a modified note's scalar fields.

        When ``expected_version`` is supplied the adapter must perform an
        atomic compare-and-set and raise
        :class:`~notes_service.domain.errors.VersionConflict` if the stored
        version differs. This guards against lost updates under concurrency.
        """
        ...

    def delete(self, note: Note) -> None: ...

    def list_accessible(
        self,
        user_id: UserId,
        *,
        limit: int,
        offset: int,
        sort: str,
    ) -> list[Note]: ...

    def count_accessible(self, user_id: UserId) -> int: ...


@runtime_checkable
class NotePermissionRepository(Protocol):
    """Persistence port for permission grants, viewed independently of notes.

    The :class:`Note` aggregate owns its permission set in memory, but some use
    cases (for example listing who a note is shared with) benefit from querying
    grants directly without loading every note.
    """

    def upsert(self, permission: NotePermission) -> None:
        """Insert a grant, or update its level if one already exists."""
        ...

    def get(self, note_id: NoteId, user_id: UserId) -> NotePermission | None: ...

    def list_for_note(self, note_id: NoteId) -> list[NotePermission]: ...

    def remove(self, note_id: NoteId, user_id: UserId) -> bool: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Transaction boundary and repository factory.

    A Unit of Work is created per request (or per use case). Repositories
    obtained from it share one transaction; nothing is durably written until
    :meth:`commit` succeeds. The context manager guarantees rollback on error.
    """

    users: UserRepository
    external_identities: ExternalIdentityRepository
    notes: NoteRepository
    permissions: NotePermissionRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
