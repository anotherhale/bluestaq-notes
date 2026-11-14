"""Folder endpoints under ``/api/v1/folders``.

Folders are owner-scoped organisation, never a sharing boundary: every endpoint
here operates only on the caller's own folders, and a folder belonging to
someone else is reported as 404 (not 403) so ids cannot be probed.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from notes_service.domain.notes.value_objects import FolderId
from notes_service.presentation.dependencies import ContainerDep, PrincipalDep, RateLimitDep
from notes_service.presentation.schemas import (
    FolderCreateRequest,
    FolderListResponse,
    FolderRenameRequest,
    FolderResponse,
    PageMeta,
)

router = APIRouter(prefix="/folders", tags=["folders"])


@router.get(
    "",
    response_model=FolderListResponse,
    summary="List the caller's folders",
    dependencies=[RateLimitDep],
)
def list_folders(principal: PrincipalDep, container: ContainerDep) -> FolderListResponse:
    folders = container.list_folders().run(principal)
    items = [FolderResponse.from_domain(folder) for folder in folders]
    return FolderListResponse(
        items=items, meta=PageMeta(total=len(items), limit=len(items), offset=0)
    )


@router.post(
    "",
    response_model=FolderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a folder owned by the caller",
    dependencies=[RateLimitDep],
)
def create_folder(
    payload: FolderCreateRequest, principal: PrincipalDep, container: ContainerDep
) -> FolderResponse:
    folder = container.create_folder().run(principal, name=payload.name)
    return FolderResponse.from_domain(folder)


@router.patch(
    "/{folder_id}",
    response_model=FolderResponse,
    summary="Rename a folder the caller owns",
    dependencies=[RateLimitDep],
)
def rename_folder(
    folder_id: UUID,
    payload: FolderRenameRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> FolderResponse:
    folder = container.rename_folder().run(
        principal, folder_id=FolderId(str(folder_id)), name=payload.name
    )
    return FolderResponse.from_domain(folder)


@router.delete(
    "/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a folder the caller owns (its notes move to the root)",
    dependencies=[RateLimitDep],
)
def delete_folder(folder_id: UUID, principal: PrincipalDep, container: ContainerDep) -> Response:
    container.delete_folder().run(principal, folder_id=FolderId(str(folder_id)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
