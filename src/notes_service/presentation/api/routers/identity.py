"""``GET /api/v1/me`` — resolve the authenticated caller."""

from __future__ import annotations

from fastapi import APIRouter

from notes_service.presentation.dependencies import (
    ContainerDep,
    PrincipalDep,
    RateLimitDep,
)
from notes_service.presentation.schemas import CurrentUserResponse

router = APIRouter(tags=["identity"])


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    summary="Resolve the current user",
    dependencies=[RateLimitDep],
)
def get_me(principal: PrincipalDep, container: ContainerDep) -> CurrentUserResponse:
    user = container.get_current_user().run(principal)
    return CurrentUserResponse.from_domain(user)
