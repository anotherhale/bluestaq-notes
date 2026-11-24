"""Repository and Unit-of-Work ports.

Repositories expose collection-like access to aggregates. They never commit;
transaction boundaries are owned exclusively by the :class:`UnitOfWork`. This
keeps atomicity decisions in the application layer where the use case is known.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from notes_service.application.ports.organization_repositories import (
    OrganizationMembershipRepository,
    OrganizationRepository,
    OrganizationUnitRepository,
    TeamMembershipRepository,
    TeamRepository,
)
from notes_service.domain.identity.entities import ExternalIdentity, User
from notes_service.domain.identity.value_objects import (
    Email,
    ExternalIdentityId,
    ProviderSubject,
    UserId,
)
from notes_service.domain.notes.folder import Folder
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.permission import NotePermission
from notes_service.domain.notes.value_objects import FolderId, NoteId


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

    def save_placement(self, note: Note) -> None:
        """Persist only the note's folder placement (and ``updated_at``).

        Placement is not a content revision, so this must not touch ``version``
        nor enforce optimistic concurrency; it is a separate write so that a
        move can never clobber a concurrent content edit.
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
        query: str | None = None,
        view: str = "all",
        folder_id: FolderId | None = None,
        starred_only: bool = False,
    ) -> list[Note]:
        """List notes the caller may read, filtered and ordered for a view.

        ``view`` is one of ``all`` (owned + shared), ``owned``, ``shared``.
        ``folder_id`` restricts to one folder; it is only meaningful for owned
        notes because a collaborator has no view of the owner's folders.
        ``starred_only`` restricts to notes the caller has personally starred.
        ``query`` performs a case-insensitive title/content substring match.
        """
        ...

    def count_accessible(
        self,
        user_id: UserId,
        *,
        query: str | None = None,
        view: str = "all",
        folder_id: FolderId | None = None,
        starred_only: bool = False,
    ) -> int: ...


@runtime_checkable
class FolderRepository(Protocol):
    """Persistence port for the :class:`Folder` aggregate."""

    def add(self, folder: Folder) -> None: ...

    def get(self, folder_id: FolderId) -> Folder | None: ...

    def save(self, folder: Folder) -> None: ...

    def delete(self, folder: Folder) -> None: ...

    def list_for_owner(self, owner_id: UserId) -> list[Folder]: ...

    def name_exists(self, owner_id: UserId, name: str) -> bool: ...


@runtime_checkable
class NoteStarRepository(Protocol):
    """Persistence port for per-user note stars."""

    def add(self, note_id: NoteId, user_id: UserId) -> None: ...

    def remove(self, note_id: NoteId, user_id: UserId) -> bool: ...

    def exists(self, note_id: NoteId, user_id: UserId) -> bool: ...


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
    folders: FolderRepository
    stars: NoteStarRepository
    organizations: OrganizationRepository
    organization_units: OrganizationUnitRepository
    teams: TeamRepository
    organization_memberships: OrganizationMembershipRepository
    team_memberships: TeamMembershipRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
