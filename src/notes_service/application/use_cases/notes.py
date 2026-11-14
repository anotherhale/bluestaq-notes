"""Note lifecycle use cases: create, read, list, update, delete, move, star."""

from __future__ import annotations

from dataclasses import dataclass

from notes_service.application.errors import AccessDenied, Conflict, InvalidInput, ResourceNotFound
from notes_service.application.ports.authorization import Action
from notes_service.application.use_cases.base import UseCase
from notes_service.domain.errors import VersionConflict
from notes_service.domain.identity.principal import Principal
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.value_objects import FolderId, NoteId, NoteVersion

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100
SORT_OPTIONS = ("updated_at", "-updated_at", "created_at", "-created_at", "title", "-title")
# Views the list endpoint understands. ``all`` is owned+shared; ``owned`` and
# ``shared`` split them so the UI can implement "My Drive" and "Shared with me"
# without client-side filtering (which would be unsafe for large sets).
VIEW_OPTIONS = ("all", "owned", "shared")
MAX_SEARCH_LENGTH = 200


@dataclass(frozen=True, slots=True)
class NotePage:
    """A page of notes plus the total count for pagination metadata."""

    items: list[Note]
    total: int


class CreateNote(UseCase):
    """Create a note owned by the authenticated principal.

    Ownership is taken from the principal only; it is never read from the
    request body.
    """

    def run(
        self,
        principal: Principal,
        *,
        title: str,
        content: str,
        folder_id: FolderId | None = None,
    ) -> Note:
        with self._uow_factory() as uow:
            if folder_id is not None:
                # A note may only be created inside a folder the caller owns.
                folder = uow.folders.get(folder_id)
                if folder is None or not folder.is_owned_by(principal.user_id):
                    raise InvalidInput("folder does not exist or is not owned by the caller")
            note = Note.create(
                owner_id=principal.user_id,
                title=title,
                content=content,
                folder_id=folder_id,
            )
            uow.notes.add(note)
            uow.commit()
            return note


class GetNote(UseCase):
    """Retrieve a single note the caller may read."""

    def run(self, principal: Principal, *, note_id: NoteId) -> Note:
        with self._uow_factory() as uow:
            note = self._load_note_for_read(uow, principal=principal, note_id=note_id)
            note.is_starred = uow.stars.exists(note_id, principal.user_id)
            return note


class ListNotes(UseCase):
    """List notes owned by or shared with the caller, filtered for a view.

    Filtering happens in the repository query (SQL), never in Python over a
    fetched page: a search must respect authorization at the database level and
    must not depend on how much of the result set the client happened to load.
    """

    def run(
        self,
        principal: Principal,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
        sort: str = "-updated_at",
        query: str | None = None,
        view: str = "all",
        folder_id: FolderId | None = None,
        starred: bool = False,
    ) -> NotePage:
        if sort not in SORT_OPTIONS:
            sort = "-updated_at"
        if view not in VIEW_OPTIONS:
            view = "all"
        normalised_query = (query or "").strip() or None
        if normalised_query is not None and len(normalised_query) > MAX_SEARCH_LENGTH:
            normalised_query = normalised_query[:MAX_SEARCH_LENGTH]

        with self._uow_factory() as uow:
            items = uow.notes.list_accessible(
                principal.user_id,
                limit=limit,
                offset=offset,
                sort=sort,
                query=normalised_query,
                view=view,
                folder_id=folder_id,
                starred_only=starred,
            )
            total = uow.notes.count_accessible(
                principal.user_id,
                query=normalised_query,
                view=view,
                folder_id=folder_id,
                starred_only=starred,
            )
            return NotePage(items=items, total=total)


class UpdateNote(UseCase):
    """Update a note's title/content with optimistic concurrency control."""

    def run(
        self,
        principal: Principal,
        *,
        note_id: NoteId,
        title: str,
        content: str,
        expected_version: NoteVersion,
    ) -> Note:
        with self._uow_factory() as uow:
            note = uow.notes.get(note_id)
            # Unreadable notes are hidden as 404; readable-but-unwritable notes
            # are a 403, because the caller already knows the note exists.
            if note is None:
                raise ResourceNotFound("note not found")
            decision = self._authorization.decide(
                principal=principal, action=Action.UPDATE, note=note
            )
            if not decision.allowed:
                read_decision = self._authorization.decide(
                    principal=principal, action=Action.READ, note=note
                )
                if not read_decision.allowed:
                    raise ResourceNotFound("note not found")
                raise AccessDenied("you do not have permission to modify this note")

            try:
                note.update_content(
                    actor_id=principal.user_id,
                    title=title,
                    content=content,
                    expected_version=expected_version,
                    now=self._clock.now(),
                )
            except VersionConflict as exc:
                raise Conflict(
                    f"note has been modified by another client; current version is {exc.actual}"
                ) from exc

            try:
                uow.notes.save(note, expected_version=int(expected_version))
            except VersionConflict as exc:
                raise Conflict(
                    f"note has been modified by another client; current version is {exc.actual}"
                ) from exc
            uow.commit()
            return note


class DeleteNote(UseCase):
    """Delete a note. Owner only."""

    def run(self, principal: Principal, *, note_id: NoteId) -> None:
        with self._uow_factory() as uow:
            note = uow.notes.get(note_id)
            if note is None:
                raise ResourceNotFound("note not found")
            decision = self._authorization.decide(
                principal=principal, action=Action.DELETE, note=note
            )
            if not decision.allowed:
                read_decision = self._authorization.decide(
                    principal=principal, action=Action.READ, note=note
                )
                if not read_decision.allowed:
                    raise ResourceNotFound("note not found")
                raise AccessDenied("only the note owner may delete this note")
            uow.notes.delete(note)
            uow.commit()


class MoveNote(UseCase):
    """Move a note into a folder (or to the root). Owner only.

    A collaborator, even with WRITE, may not reorganise the owner's notes: the
    move is gated on ownership, not on write access. The destination folder must
    belong to the note's owner, so a note can never be moved into someone
    else's folder.
    """

    def run(
        self,
        principal: Principal,
        *,
        note_id: NoteId,
        folder_id: FolderId | None,
    ) -> Note:
        with self._uow_factory() as uow:
            note = uow.notes.get(note_id)
            if note is None:
                raise ResourceNotFound("note not found")
            decision = self._authorization.decide(
                principal=principal, action=Action.MOVE, note=note
            )
            if not decision.allowed:
                read_decision = self._authorization.decide(
                    principal=principal, action=Action.READ, note=note
                )
                if not read_decision.allowed:
                    raise ResourceNotFound("note not found")
                raise AccessDenied("only the note owner may move this note")

            if folder_id is not None:
                folder = uow.folders.get(folder_id)
                if folder is None or folder.owner_id != note.owner_id:
                    # The destination must be one of the *owner's* folders; a
                    # folder belonging to anyone else is a bad request, not a
                    # silent no-op.
                    raise InvalidInput("destination folder does not exist for this note's owner")

            note.move_to_folder(
                actor_id=principal.user_id, folder_id=folder_id, now=self._clock.now()
            )
            uow.notes.save_placement(note)
            note.is_starred = uow.stars.exists(note_id, principal.user_id)
            uow.commit()
            return note


class StarNote(UseCase):
    """Star a note for the caller. Requires read access, not ownership.

    A star is personal, so any user who can read a note may star it; it never
    affects other collaborators.
    """

    def run(self, principal: Principal, *, note_id: NoteId) -> Note:
        with self._uow_factory() as uow:
            note = self._load_note_for_read(uow, principal=principal, note_id=note_id)
            uow.stars.add(note_id, principal.user_id)
            note.is_starred = True
            uow.commit()
            return note


class UnstarNote(UseCase):
    """Remove the caller's star from a note. Idempotent."""

    def run(self, principal: Principal, *, note_id: NoteId) -> Note:
        with self._uow_factory() as uow:
            note = self._load_note_for_read(uow, principal=principal, note_id=note_id)
            uow.stars.remove(note_id, principal.user_id)
            note.is_starred = False
            uow.commit()
            return note
