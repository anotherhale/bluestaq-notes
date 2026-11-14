"""Note CRUD endpoints under ``/api/v1/notes``."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from notes_service.domain.notes.value_objects import FolderId, NoteId, NoteVersion
from notes_service.presentation.dependencies import (
    ContainerDep,
    PaginationDep,
    PrincipalDep,
    RateLimitDep,
)
from notes_service.presentation.schemas import (
    NoteCreateRequest,
    NoteListResponse,
    NoteMoveRequest,
    NoteResponse,
    NoteSummaryResponse,
    NoteUpdateRequest,
    PageMeta,
)

router = APIRouter(prefix="/notes", tags=["notes"])


@router.get(
    "",
    response_model=NoteListResponse,
    summary="List notes the caller can read, filtered and sorted",
    dependencies=[RateLimitDep],
)
def list_notes(
    principal: PrincipalDep,
    container: ContainerDep,
    page: PaginationDep,
    sort: Annotated[str, Query()] = "-updated_at",
    q: Annotated[str | None, Query(max_length=200)] = None,
    view: Annotated[str, Query()] = "all",
    folder_id: Annotated[UUID | None, Query()] = None,
    starred: Annotated[bool, Query()] = False,
) -> NoteListResponse:
    """List notes, filtered by view/folder/starred and a title/content search.

    All filtering is applied server-side against the caller's authorized set:
    the client never receives a note it cannot read, so it cannot filter
    unauthorized notes in the browser.
    """
    result = container.list_notes().run(
        principal,
        limit=page.limit,
        offset=page.offset,
        sort=sort,
        query=q,
        view=view,
        folder_id=FolderId(str(folder_id)) if folder_id is not None else None,
        starred=starred,
    )
    return NoteListResponse(
        items=[
            NoteSummaryResponse.from_domain(note, caller_id=principal.user_id)
            for note in result.items
        ],
        meta=PageMeta(total=result.total, limit=page.limit, offset=page.offset),
    )


@router.post(
    "",
    response_model=NoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a private note owned by the caller",
    dependencies=[RateLimitDep],
)
def create_note(
    payload: NoteCreateRequest, principal: PrincipalDep, container: ContainerDep
) -> NoteResponse:
    note = container.create_note().run(
        principal,
        title=payload.title,
        content=payload.content,
        folder_id=FolderId(str(payload.folder_id)) if payload.folder_id is not None else None,
    )
    return NoteResponse.from_domain(note, caller_id=principal.user_id)


@router.get(
    "/{note_id}",
    response_model=NoteResponse,
    summary="Retrieve a note",
    dependencies=[RateLimitDep],
)
def get_note(note_id: UUID, principal: PrincipalDep, container: ContainerDep) -> NoteResponse:
    note = container.get_note().run(principal, note_id=NoteId(str(note_id)))
    return NoteResponse.from_domain(note, caller_id=principal.user_id)


@router.put(
    "/{note_id}",
    response_model=NoteResponse,
    summary="Update a note (optimistic concurrency via version)",
    dependencies=[RateLimitDep],
)
def update_note(
    note_id: UUID,
    payload: NoteUpdateRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> NoteResponse:
    note = container.update_note().run(
        principal,
        note_id=NoteId(str(note_id)),
        title=payload.title,
        content=payload.content,
        expected_version=NoteVersion(payload.version),
    )
    return NoteResponse.from_domain(note, caller_id=principal.user_id)


@router.patch(
    "/{note_id}/folder",
    response_model=NoteResponse,
    summary="Move a note into a folder, or to the root (owner only)",
    dependencies=[RateLimitDep],
)
def move_note(
    note_id: UUID,
    payload: NoteMoveRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> NoteResponse:
    note = container.move_note().run(
        principal,
        note_id=NoteId(str(note_id)),
        folder_id=FolderId(str(payload.folder_id)) if payload.folder_id is not None else None,
    )
    return NoteResponse.from_domain(note, caller_id=principal.user_id)


@router.put(
    "/{note_id}/star",
    response_model=NoteResponse,
    summary="Star a note for the caller (idempotent)",
    dependencies=[RateLimitDep],
)
def star_note(note_id: UUID, principal: PrincipalDep, container: ContainerDep) -> NoteResponse:
    note = container.star_note().run(principal, note_id=NoteId(str(note_id)))
    return NoteResponse.from_domain(note, caller_id=principal.user_id)


@router.delete(
    "/{note_id}/star",
    response_model=NoteResponse,
    summary="Remove the caller's star from a note (idempotent)",
    dependencies=[RateLimitDep],
)
def unstar_note(note_id: UUID, principal: PrincipalDep, container: ContainerDep) -> NoteResponse:
    note = container.unstar_note().run(principal, note_id=NoteId(str(note_id)))
    return NoteResponse.from_domain(note, caller_id=principal.user_id)


@router.delete(
    "/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a note (owner only)",
    dependencies=[RateLimitDep],
)
def delete_note(note_id: UUID, principal: PrincipalDep, container: ContainerDep) -> Response:
    container.delete_note().run(principal, note_id=NoteId(str(note_id)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
