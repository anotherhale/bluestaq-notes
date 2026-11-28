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

from notes_service.application.errors import ResourceNotFound
from notes_service.application.use_cases.operator_access_report import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
)
from notes_service.application.use_cases.operator_grants import (
    IssueOperatorGrantRequest,
    RevokeOperatorGrantRequest,
)
from notes_service.domain.identity.operator_grant import OperatorGrantRationale
from notes_service.domain.identity.value_objects import UserId
from notes_service.presentation.dependencies import ContainerDep, PrincipalDep, RateLimitDep
from notes_service.presentation.schemas import (
    ChainAuditReportResponse,
    OperatorAccessPurgeResponse,
    OperatorAccessReportResponse,
    OperatorGrantIssueRequest,
    OperatorGrantReportResponse,
    OperatorGrantResponse,
    OperatorGrantRevokedResponse,
    OperatorGrantRevokeRequest,
    RevokedOperatorGrantReportResponse,
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
    principal: PrincipalDep,
    container: ContainerDep,
    offset: Annotated[int, Query(ge=0, description="Agents to skip, oldest first.")] = 0,
) -> ChainAuditReportResponse:
    """Recompute every agent's audit chain and report each contradiction.

    The per-agent endpoint (``/agents/{id}/tool-invocations/verification``) lets
    an owner check their own agent. This is the platform's own check: one call
    that walks every chain, so a broken trail is found by the operator rather
    than waiting for the affected owner to look. ``verified`` is true only for a
    *complete* sweep with no findings; a capped sweep reports ``truncated`` and
    is never ``verified``, so a partial pass cannot be read as an all-clear.

    ``offset`` continues a capped sweep. The cap bounds one response, not the
    check: without a cursor the agents past it were never verified, and since
    the sweep walks oldest-first those were the newest chains. Follow
    ``next_offset`` until it is null to reach every agent; that is the only way
    the last page can report ``verified``, because any page after the first is
    never ``verified`` on its own.
    """
    report = container.sweep_audit_integrity().run(principal, offset=offset)
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


@router.post(
    "/operator-access-log/purge",
    response_model=OperatorAccessPurgeResponse,
    summary="Purge access-log entries older than the retention window (operators only)",
    dependencies=[RateLimitDep],
)
def purge_operator_access_log(
    principal: PrincipalDep, container: ContainerDep
) -> OperatorAccessPurgeResponse:
    """Delete access-log entries that have aged out of the configured window.

    The record of a privileged read is durable since NEX-072 and, until this
    endpoint, nothing pruned it — an unbounded audit table is a slow failure. The
    window comes from configuration and is bounded by the domain policy on both
    sides, so this endpoint cannot be asked for a shorter window, a specific user's
    entries, or a cutoff of the caller's choosing: it can only apply the retention
    the deployment already declared.

    Gated on ``access-log-retention``, deliberately not the read scope
    ``operator-access-report`` — erasing the trail is a graver authority than
    reading it. The purge is itself written to the log before it runs, stamped
    inside the window, so it cannot delete the record of its own action. A
    non-operator gets ``404`` like every other ops endpoint, and a deployment with
    retention disabled gets ``404`` too: the endpoint exists only where a window
    does.
    """
    use_case = container.purge_operator_access_log()
    if use_case is None:
        raise ResourceNotFound("not found")
    return OperatorAccessPurgeResponse.from_result(use_case.execute(principal))


@router.get(
    "/operator-grants",
    response_model=OperatorGrantReportResponse,
    summary="Every currently-active operator grant (operators only)",
    dependencies=[RateLimitDep],
)
def list_operator_grants(
    principal: PrincipalDep, container: ContainerDep
) -> OperatorGrantReportResponse:
    """Return who currently holds an operator grant, and until when.

    This is the question the static allowlist structurally could not answer: it
    held strings, not holders. Each row names a user, the scopes they hold right
    now, and the nearest deadline among those grants (``null`` when they hold an
    unbounded grant). Expired grants are absent — they confer nothing, so listing
    them as holdings would overstate the allowlist.

    Reading this view is itself a granted operator read and is recorded, exactly
    like the access report. A non-operator gets ``404`` like every other ops
    endpoint.
    """
    snapshot = container.list_operator_grants().run(principal)
    return OperatorGrantReportResponse.from_snapshot(snapshot)


@router.post(
    "/operator-grants",
    response_model=OperatorGrantResponse,
    status_code=201,
    summary="Issue a bounded operator grant to another user (operators only)",
    dependencies=[RateLimitDep],
)
def issue_operator_grant(
    body: OperatorGrantIssueRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> OperatorGrantResponse:
    """Hand a named operator scope to another user, until a stated deadline.

    The counterpart to the read-only holder report: the platform could list grants
    but never create one, so the bounded, attributable grant its record was built
    for was unreachable. The issuer is the caller — the body carries no
    ``granted_by`` — and the use case refuses a self-grant, an unbounded deadline,
    a scope the issuer does not itself hold, and an unknown scope. A non-operator
    gets ``404`` like every other ops endpoint.

    The body must also state *why*: ``rationale`` is the operator's recorded
    reason, stored on the grant so an auditor can read the justification, not
    just the fact. A blank or over-long reason is a ``422`` at the boundary; the
    domain value object enforces the same bound for every other caller.
    """
    grant = container.issue_operator_grant().execute(
        principal,
        IssueOperatorGrantRequest(
            user_id=UserId.parse(body.user_id),
            scope=body.scope,
            expires_at=body.expires_at,
            rationale=OperatorGrantRationale(body.rationale),
        ),
    )
    return OperatorGrantResponse.from_domain(grant)


@router.post(
    "/operator-grants/revoke",
    response_model=OperatorGrantRevokedResponse,
    summary="Revoke a user's operator grant of one scope (operators only)",
    dependencies=[RateLimitDep],
)
def revoke_operator_grant(
    body: OperatorGrantRevokeRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> OperatorGrantRevokedResponse:
    """Take a named operator scope away from another user, effective immediately.

    The undo of the issue endpoint. Until this existed a grant could only end by
    expiry, so a mistaken hand-out stayed live for its whole term and a
    *configuration* grant — unbounded, reviewed at deploy time — could not be ended
    without editing the deployment seed. A revocation is recorded on the grant as
    an attributable fact (who, when) rather than applied by deleting it, so the
    history of who held what, and who removed it, survives the revocation.

    The caller must be a peer, never the holder: a self-revoke is refused with
    ``409``, the same separation of duties that stops a self-grant. Revoking
    authority nobody holds is also ``409`` rather than a cheerful ``200`` — a
    responder must not be told authority was removed when it had already lapsed or
    the wrong user was named. A non-operator gets ``404`` like every other ops
    endpoint.
    """
    revoked = container.revoke_operator_grant().execute(
        principal,
        RevokeOperatorGrantRequest(
            user_id=UserId.parse(body.user_id),
            scope=body.scope,
        ),
    )
    return OperatorGrantRevokedResponse(
        user_id=body.user_id,
        scope=body.scope,
        grants_revoked=revoked,
    )


@router.get(
    "/operator-grants/revoked",
    response_model=RevokedOperatorGrantReportResponse,
    summary="Every revoked operator grant, with who revoked it and when (operators only)",
    dependencies=[RateLimitDep],
)
def list_revoked_operator_grants(
    principal: PrincipalDep, container: ContainerDep
) -> RevokedOperatorGrantReportResponse:
    """Return every grant that ended by revocation, and its revocation provenance.

    NEX-079 recorded a revocation as an attributable fact on the grant —
    ``revoked_at`` and ``revoked_by`` — so that "who removed whose authority, and
    when" would be answerable. Nothing read it back: the write was durable and the
    question stayed unanswerable except by querying the database by hand. This
    endpoint is the read half, the same loop NEX-076/NEX-077 closed for a grant's
    ``rationale``.

    Live grants and grants that merely expired are absent: this is revoked history,
    not a holdings report. A revoked grant whose revoker's account was later deleted
    still appears, with ``revoked_by: null`` — the revocation outlives its revoker.

    Reading this is a gated operator read under the same scope as the holder report,
    recorded like every other ops read. A non-operator gets ``404``.
    """
    snapshot = container.list_revoked_operator_grants().run(principal)
    return RevokedOperatorGrantReportResponse.from_snapshot(snapshot)
