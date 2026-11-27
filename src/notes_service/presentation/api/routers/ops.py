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

from fastapi import APIRouter

from notes_service.presentation.dependencies import ContainerDep, PrincipalDep, RateLimitDep
from notes_service.presentation.schemas import (
    ChainAuditReportResponse,
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
