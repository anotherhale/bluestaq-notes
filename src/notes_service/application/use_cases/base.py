"""Shared plumbing for use cases."""

from __future__ import annotations

from collections.abc import Callable

from notes_service.application.errors import AccessDenied, ResourceNotFound
from notes_service.application.ports.authorization import Action, AuthorizationProvider
from notes_service.application.ports.clock import Clock
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.domain.identity.principal import Principal
from notes_service.domain.notes.note import Note
from notes_service.domain.notes.value_objects import NoteId

UnitOfWorkFactory = Callable[[], UnitOfWork]


class UseCase:
    """Base class holding the dependencies every use case shares."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        authorization: AuthorizationProvider,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._authorization = authorization
        self._clock = clock

    def _load_note_for_read(
        self, uow: UnitOfWork, *, principal: Principal, note_id: NoteId
    ) -> Note:
        """Load a note the caller is allowed to read, else 404.

        A missing note and a note the caller cannot read are indistinguishable
        to the client, which prevents probing for the existence of other users'
        private notes.
        """
        note = uow.notes.get(note_id)
        if note is None:
            raise ResourceNotFound("note not found")
        decision = self._authorization.decide(principal=principal, action=Action.READ, note=note)
        if not decision.allowed:
            raise ResourceNotFound("note not found")
        return note

    def _load_note_for_action(
        self,
        uow: UnitOfWork,
        *,
        principal: Principal,
        note_id: NoteId,
        action: Action,
        denied_message: str,
    ) -> Note:
        """Load a note for a mutating action, splitting 404 from 403 (ADR-008).

        The whole non-enumeration rule lives here so each call site states only
        its intent. A caller who cannot *read* the note gets 404 -- identical to
        an absent note -- so a denied action never confirms existence. A caller
        who can read but not perform ``action`` gets 403, because it already
        knows the note exists.
        """
        note = self._load_note_for_read(uow, principal=principal, note_id=note_id)
        decision = self._authorization.decide(principal=principal, action=action, note=note)
        if not decision.allowed:
            raise AccessDenied(denied_message)
        return note
