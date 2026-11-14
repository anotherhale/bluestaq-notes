"""``GET /api/v1/users`` — list users available for sharing."""

from __future__ import annotations

from fastapi import APIRouter

from notes_service.presentation.dependencies import (
    ContainerDep,
    PaginationDep,
    PrincipalDep,
    RateLimitDep,
)
from notes_service.presentation.schemas import PageMeta, UserListResponse, UserResponse

router = APIRouter(tags=["users"])


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="List users available for sharing",
    dependencies=[RateLimitDep],
)
def list_users(
    principal: PrincipalDep,
    container: ContainerDep,
    page: PaginationDep,
) -> UserListResponse:
    use_case = container.list_users()
    users = use_case.run(principal, limit=page.limit, offset=page.offset)
    total = use_case.count(principal)
    return UserListResponse(
        items=[UserResponse.from_domain(user) for user in users],
        meta=PageMeta(total=total, limit=page.limit, offset=page.offset),
    )
