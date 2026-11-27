"""Operator endpoints under ``/api/v1/ops``.

These report on the process as a whole rather than on the caller's own data, so
they are not ordinary user endpoints. Access is an explicit allowlist
(``OPS_OPERATOR_USER_IDS``) rather than a side effect of being authenticated, and
the list is empty by default: an unconfigured deployment exposes nothing.

A caller who is not an operator receives ``404``, not ``403``. The distinction
matters: ``403`` would confirm the endpoint exists and that the caller merely
lacks the role, which is itself a small disclosure to an ordinary user probing
the API surface.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from notes_service.application.use_cases.operator_access_report import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
)
from notes_service.presentation.dependencies import ContainerDep, PrincipalDep, RateLimitDep
from notes_service.presentation.schemas import (
    ChainAuditReportResponse,
    OperatorAccessReportResponse,
    WithholdingReportResponse,
)

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get(
    "/withholding-report",
    response_model=WithholdingReportResponse,
    summary="Aggregate advisory withholding totals (operators only)",
    dependencies=[RateLimitDep],
)
def get_withholding_report(
    principal: PrincipalDep, container: ContainerDep
) -> WithholdingReportResponse:
    """Return process-wide withholding totals for an authorized operator.

    ``over_cap_rate`` answers R34: how often an advisory was computed on less
    context than the caller held. It is a platform health signal, not user data.
    """
    snapshot = container.get_withholding_report().run(principal)
    return WithholdingReportResponse.from_snapshot(snapshot)


@router.get(
    "/audit-integrity",
    response_model=ChainAuditReportResponse,
    summary="Verify every agent's audit chain in the deployment (operators only)",
    dependencies=[RateLimitDep],
)
def sweep_audit_integrity(
    principal: PrincipalDep, container: ContainerDep
) -> ChainAuditReportResponse:
    """Recompute every agent's audit chain and report each contradiction.

    The per-agent endpoint (``/agents/{id}/tool-invocations/verification``) lets
    an owner check their own agent. This is the platform's own check: one call
    that walks every chain, so a broken trail is found by the operator rather
    than waiting for the affected owner to look. ``verified`` is true only for a
    *complete* sweep with no findings; a capped sweep reports ``truncated`` and
    is never ``verified``, so a partial pass cannot be read as an all-clear.
    """
    report = container.sweep_audit_integrity().run(principal)
    return ChainAuditReportResponse.from_report(report)


@router.get(
    "/access-report",
    response_model=OperatorAccessReportResponse,
    summary="Recent granted operational reads (operators only)",
    dependencies=[RateLimitDep],
)
def get_operator_access_report(
    principal: PrincipalDep,
    container: ContainerDep,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_LIMIT,
            description=(
                "Maximum entries to return, newest first. Values above the "
                "ceiling are rejected rather than silently clamped, so a caller "
                "cannot believe it read more than the cap allows."
            ),
        ),
    ] = DEFAULT_LIMIT,
) -> OperatorAccessReportResponse:
    """Return who has used an operator grant, newest first.

    This is the read side of the record R36 added. Without it the log answered
    nothing: every granted platform-wide read was written and never read back, so
    "who read the totals" was unanswerable from inside the system.

    Reading this view is itself a granted operator read, so it appends an entry
    of its own — the trail cannot be inspected without being extended. A
    non-operator gets ``404`` like every other ops endpoint.
    """
    snapshot = container.get_operator_access_report().run(principal, limit=limit)
    return OperatorAccessReportResponse.from_snapshot(snapshot)
