"""Permission endpoints under ``/api/v1/notes/{note_id}/permissions``."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from notes_service.domain.identity.value_objects import UserId
from notes_service.domain.notes.value_objects import NoteId
from notes_service.presentation.dependencies import ContainerDep, PrincipalDep, RateLimitDep
from notes_service.presentation.schemas import (
    PageMeta,
    PermissionGrantRequest,
    PermissionListResponse,
    PermissionResponse,
)

router = APIRouter(prefix="/notes/{note_id}/permissions", tags=["permissions"])


@router.get(
    "",
    response_model=PermissionListResponse,
    summary="List collaborators on a note (owner only)",
    dependencies=[RateLimitDep],
)
def list_permissions(
    note_id: UUID, principal: PrincipalDep, container: ContainerDep
) -> PermissionListResponse:
    permissions = container.list_permissions().run(principal, note_id=NoteId(str(note_id)))
    items = [PermissionResponse.from_domain(permission) for permission in permissions]
    return PermissionListResponse(
        items=items, meta=PageMeta(total=len(items), limit=len(items), offset=0)
    )


@router.post(
    "",
    response_model=PermissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant or upgrade a collaborator's access (owner only)",
    dependencies=[RateLimitDep],
)
def grant_permission(
    note_id: UUID,
    payload: PermissionGrantRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> PermissionResponse:
    permission = container.share_note().run(
        principal,
        note_id=NoteId(str(note_id)),
        target_user_id=UserId(str(payload.user_id)),
        level=payload.permission,
    )
    return PermissionResponse.from_domain(permission)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a collaborator's access (owner only)",
    dependencies=[RateLimitDep],
)
def revoke_permission(
    note_id: UUID, user_id: UUID, principal: PrincipalDep, container: ContainerDep
) -> Response:
    container.revoke_permission().run(
        principal, note_id=NoteId(str(note_id)), target_user_id=UserId(str(user_id))
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
