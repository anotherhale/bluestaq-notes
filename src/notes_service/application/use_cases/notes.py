"""Note lifecycle use cases: create, read, list, update, delete."""

from __future__ import annotations

from dataclasses import dataclass

from notes_service.application.errors import AccessDenied, Conflict, ResourceNotFound
from notes_service.application.ports.authorization import Action
from notes_service.application.use_cases.base import UseCase
from notes_service.domain.errors import VersionConflict
from notes_service.domain.identity.principal import Principal
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.value_objects import NoteId, NoteVersion

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100
SORT_OPTIONS = ("updated_at", "-updated_at", "created_at", "-created_at", "title", "-title")


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

    def run(self, principal: Principal, *, title: str, content: str) -> Note:
        with self._uow_factory() as uow:
            note = Note.create(owner_id=principal.user_id, title=title, content=content)
            uow.notes.add(note)
            uow.commit()
            return note


class GetNote(UseCase):
    """Retrieve a single note the caller may read."""

    def run(self, principal: Principal, *, note_id: NoteId) -> Note:
        with self._uow_factory() as uow:
            return self._load_note_for_read(uow, principal=principal, note_id=note_id)


class ListNotes(UseCase):
    """List notes owned by or shared with the caller, without duplicates.

    A note the caller both owns and is (redundantly) shared on appears once:
    the query de-duplicates by note id and the aggregate is keyed by id.
    """

    def run(
        self,
        principal: Principal,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
        sort: str = "-updated_at",
    ) -> NotePage:
        if sort not in SORT_OPTIONS:
            sort = "-updated_at"
        with self._uow_factory() as uow:
            items = uow.notes.list_accessible(
                principal.user_id, limit=limit, offset=offset, sort=sort
            )
            total = uow.notes.count_accessible(principal.user_id)
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
