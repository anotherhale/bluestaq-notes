"""``GET /api/v1/demo/identities`` — development demo identity discovery.

A client that uses the development demo identity mechanism needs to know which
identities it may act as *before* it has authenticated, because selecting an
identity is what authenticates it. This endpoint is the bootstrap for that
selection.

It is a development-only affordance:

* it is served only when ``AUTH_DEMO_ENABLED`` is true, and never in production;
* when demo authentication is unavailable it returns ``404`` (not ``403``), so
  its existence is not advertised in a hardened deployment;
* it is unauthenticated by necessity (it exists to bootstrap authentication),
  and it is rate limited like every other endpoint;
* it returns only the active users linked to the demo provider — never
  suspended accounts, never real/OIDC users, and no fields beyond what the
  selector needs.

The identity it advertises is still validated server-side on every subsequent
request by the ``DemoIdentityProvider``; this endpoint grants no access by
itself.
"""

from __future__ import annotations

from fastapi import APIRouter

from notes_service.application.errors import ResourceNotFound
from notes_service.infrastructure.identity.demo_provider import DEMO_HEADER
from notes_service.presentation.dependencies import (
    AnonymousRateLimitDep,
    ContainerDep,
    PaginationDep,
)
from notes_service.presentation.schemas import (
    DemoIdentityListResponse,
    DemoIdentityResponse,
    PageMeta,
)

router = APIRouter(prefix="/demo", tags=["identity"])


@router.get(
    "/identities",
    response_model=DemoIdentityListResponse,
    summary="List development demo identities (demo mode only)",
    dependencies=[AnonymousRateLimitDep],
)
def list_demo_identities(
    container: ContainerDep,
    page: PaginationDep,
) -> DemoIdentityListResponse:
    if not container.settings.auth_demo_enabled:
        # Hide the endpoint entirely when demo authentication is off.
        raise ResourceNotFound("not found")

    use_case = container.list_demo_identities()
    users = use_case.run(limit=page.limit, offset=page.offset)
    total = use_case.count()
    return DemoIdentityListResponse(
        enabled=True,
        header=DEMO_HEADER,
        items=[DemoIdentityResponse.from_domain(user) for user in users],
        meta=PageMeta(total=total, limit=page.limit, offset=page.offset),
    )
