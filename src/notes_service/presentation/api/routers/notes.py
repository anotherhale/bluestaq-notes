"""Note CRUD endpoints under ``/api/v1/notes``."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from notes_service.domain.notes.value_objects import NoteId, NoteVersion
from notes_service.presentation.dependencies import (
    ContainerDep,
    PaginationDep,
    PrincipalDep,
    RateLimitDep,
)
from notes_service.presentation.schemas import (
    NoteCreateRequest,
    NoteListResponse,
    NoteResponse,
    NoteSummaryResponse,
    NoteUpdateRequest,
    PageMeta,
)

router = APIRouter(prefix="/notes", tags=["notes"])


@router.get(
    "",
    response_model=NoteListResponse,
    summary="List notes owned by or shared with the caller",
    dependencies=[RateLimitDep],
)
def list_notes(
    principal: PrincipalDep,
    container: ContainerDep,
    page: PaginationDep,
    sort: Annotated[str, Query()] = "-updated_at",
) -> NoteListResponse:
    result = container.list_notes().run(principal, limit=page.limit, offset=page.offset, sort=sort)
    return NoteListResponse(
        items=[NoteSummaryResponse.from_domain(note) for note in result.items],
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
    note = container.create_note().run(principal, title=payload.title, content=payload.content)
    return NoteResponse.from_domain(note)


@router.get(
    "/{note_id}",
    response_model=NoteResponse,
    summary="Retrieve a note",
    dependencies=[RateLimitDep],
)
def get_note(note_id: UUID, principal: PrincipalDep, container: ContainerDep) -> NoteResponse:
    note = container.get_note().run(principal, note_id=NoteId(str(note_id)))
    return NoteResponse.from_domain(note)


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
    return NoteResponse.from_domain(note)


@router.delete(
    "/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a note (owner only)",
    dependencies=[RateLimitDep],
)
def delete_note(note_id: UUID, principal: PrincipalDep, container: ContainerDep) -> Response:
    container.delete_note().run(principal, note_id=NoteId(str(note_id)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
