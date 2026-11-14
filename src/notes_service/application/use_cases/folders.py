"""Folder management use cases: create, list, rename, delete.

Folders are owner-scoped and are **not** a sharing boundary, so every operation
here is authorized purely by ownership: there is no notion of a "shared folder".
An inaccessible or non-existent folder is reported as 404 (never 403) so that a
caller cannot probe for the existence of another user's folders.
"""

from __future__ import annotations

from dataclasses import dataclass

from notes_service.application.errors import Conflict, ResourceNotFound
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.application.use_cases.base import UseCase
from notes_service.domain.identity.principal import Principal
from notes_service.domain.notes.folder import Folder
from notes_service.domain.notes.value_objects import FolderId

# Upper bound on the notes re-homed when a folder is deleted. The public list
# endpoint caps at 100, but this is an internal bulk query with its own bound; a
# folder holding more than this many notes is pathological for this product.
_FOLDER_DELETE_NOTE_SCAN = 10_000


@dataclass(frozen=True, slots=True)
class FolderDeletion:
    """Outcome of deleting a folder: how many notes were moved to the root."""

    moved_notes: int


def load_owned_folder(uow: UnitOfWork, *, principal: Principal, folder_id: FolderId) -> Folder:
    """Load a folder the caller owns, else 404.

    A folder owned by someone else is indistinguishable from one that does not
    exist: both are 404, so folder ids cannot be probed.
    """
    folder = uow.folders.get(folder_id)
    if folder is None or not folder.is_owned_by(principal.user_id):
        raise ResourceNotFound("folder not found")
    return folder


class CreateFolder(UseCase):
    """Create a folder owned by the caller."""

    def run(self, principal: Principal, *, name: str) -> Folder:
        with self._uow_factory() as uow:
            if uow.folders.name_exists(principal.user_id, name.strip()):
                raise Conflict("a folder with that name already exists")
            folder = Folder.create(owner_id=principal.user_id, name=name)
            uow.folders.add(folder)
            uow.commit()
            return folder


class ListFolders(UseCase):
    """List the caller's own folders."""

    def run(self, principal: Principal) -> list[Folder]:
        with self._uow_factory() as uow:
            return uow.folders.list_for_owner(principal.user_id)


class RenameFolder(UseCase):
    """Rename a folder the caller owns."""

    def run(self, principal: Principal, *, folder_id: FolderId, name: str) -> Folder:
        with self._uow_factory() as uow:
            folder = load_owned_folder(uow, principal=principal, folder_id=folder_id)
            trimmed = name.strip()
            if uow.folders.name_exists(principal.user_id, trimmed) and str(folder.name) != trimmed:
                raise Conflict("a folder with that name already exists")
            folder.rename(actor_id=principal.user_id, name=trimmed, now=self._clock.now())
            uow.folders.save(folder)
            uow.commit()
            return folder


class DeleteFolder(UseCase):
    """Delete a folder the caller owns.

    Deleting a folder never deletes notes. Notes it contained are moved back to
    the owner's root, which is the least destructive interpretation and keeps
    the "a note has one owner" invariant intact.
    """

    def run(self, principal: Principal, *, folder_id: FolderId) -> FolderDeletion:
        with self._uow_factory() as uow:
            folder = load_owned_folder(uow, principal=principal, folder_id=folder_id)
            contained = uow.notes.list_accessible(
                principal.user_id,
                limit=_FOLDER_DELETE_NOTE_SCAN,
                offset=0,
                sort="updated_at",
                view="owned",
                folder_id=folder_id,
            )
            for note in contained:
                note.folder_id = None
                uow.notes.save_placement(note)
            uow.folders.delete(folder)
            uow.commit()
            return FolderDeletion(moved_notes=len(contained))


__all__ = [
    "CreateFolder",
    "DeleteFolder",
    "FolderDeletion",
    "ListFolders",
    "RenameFolder",
    "load_owned_folder",
]
