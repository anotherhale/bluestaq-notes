"""Governance policy lifecycle under ``/api/v1/ops/policies``.

A policy is the declarative AST that decides what an agent may do, so its
lifecycle is the most consequential write surface in the platform: drafting
changes what *will* be enforced, publishing puts it in force for every agent, and
a rollback silently restores rules someone had already retired. All four
operations are therefore gated on an operator scope, and the gate lives in the
application layer, not here.

The router's only jobs are HTTP mapping and one security rule that must not live
below it: the publisher is stamped from the authenticated principal. The request
bodies carry no ``published_by`` field, so a caller cannot attribute a policy in
force to someone else — the field exists on the use-case request precisely
because only the boundary knows who the caller is.

Access failure surfaces as ``404`` from the operator gate (see ``ops.py``), so a
non-operator cannot tell these routes apart from ones that do not exist.
"""

from __future__ import annotations

from fastapi import APIRouter

from notes_service.application.use_cases.policy_lifecycle import (
    DraftPolicyVersion,
    PublishPolicyVersion,
    RollbackPolicyVersion,
)
from notes_service.domain.errors import ValidationError
from notes_service.domain.governance.policy import PolicyEffect, PolicyObligation, PolicyRule
from notes_service.infrastructure.governance.policy_codec import decode_condition
from notes_service.presentation.dependencies import ContainerDep, PrincipalDep, RateLimitDep
from notes_service.presentation.schemas import (
    PolicyDraftRequest,
    PolicyHistoryResponse,
    PolicyPublishRequest,
    PolicyRollbackRequest,
    PolicyRuleRequest,
    PolicyVersionResponse,
)

router = APIRouter(prefix="/ops/policies", tags=["ops"])


def _to_rules(requests: list[PolicyRuleRequest]) -> tuple[PolicyRule, ...]:
    """Rebuild the domain rule tuple from the wire shape.

    Decoding goes through the same codec the store uses, so a request cannot
    introduce a condition the domain refuses to *store*: validating here as well
    would be a second, drifting definition of a legal policy.
    """
    rules: list[PolicyRule] = []
    for request in requests:
        try:
            effect = PolicyEffect(request.effect)
        except ValueError as error:
            raise ValidationError(
                f"unknown policy effect {request.effect!r}; expected 'allow' or 'deny'"
            ) from error
        condition = decode_condition(request.when) if request.when is not None else None
        obligations: list[PolicyObligation] = []
        for name in request.obligations:
            try:
                obligations.append(PolicyObligation(name))
            except ValueError as error:
                raise ValidationError(
                    f"unknown obligation {name!r} on rule {request.rule_id!r}"
                ) from error
        rules.append(
            PolicyRule(
                rule_id=request.rule_id,
                effect=effect,
                when=condition,
                obligations=tuple(obligations),
            )
        )
    return tuple(rules)


@router.post(
    "/{policy_id}/versions",
    response_model=PolicyVersionResponse,
    status_code=201,
    summary="Draft the next version of a policy (operators only)",
    dependencies=[RateLimitDep],
)
def draft_policy_version(
    policy_id: str,
    body: PolicyDraftRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> PolicyVersionResponse:
    """Store a new draft. A draft is not in force and changes nothing yet."""
    version = container.draft_policy().execute(
        principal,
        DraftPolicyVersion(
            policy_id=policy_id, rules=_to_rules(body.rules), rationale=body.rationale
        ),
    )
    return PolicyVersionResponse.from_domain(version)


@router.post(
    "/{policy_id}/publish",
    response_model=PolicyVersionResponse,
    summary="Publish a policy version, putting it in force (operators only)",
    dependencies=[RateLimitDep],
)
def publish_policy_version(
    policy_id: str,
    body: PolicyPublishRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> PolicyVersionResponse:
    """Publish a stored draft and retire whatever was in force before it.

    Publishing an already-published or retired version is a ``409``: that is a
    conflict with the version's state, not a missing resource, and rewriting a
    published version's status would rewrite history.
    """
    version = container.publish_draft().execute(
        principal,
        PublishPolicyVersion(policy_id=policy_id, version=body.version),
    )
    return PolicyVersionResponse.from_domain(version)


@router.post(
    "/{policy_id}/rollback",
    response_model=PolicyVersionResponse,
    summary="Restore an earlier version's rules as a new release (operators only)",
    dependencies=[RateLimitDep],
)
def rollback_policy(
    policy_id: str,
    body: PolicyRollbackRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> PolicyVersionResponse:
    """Roll back by drafting the old rules under a *new* version number.

    The rollback is itself a version, so the fact that it happened stays in the
    history. Rolling back to a draft is a ``409``: it would put rules in force
    that nobody approved.
    """
    version = container.rollback_policy().execute(
        principal,
        RollbackPolicyVersion(
            policy_id=policy_id, to_version=body.to_version, rationale=body.rationale
        ),
    )
    return PolicyVersionResponse.from_domain(version)


@router.get(
    "/{policy_id}/versions",
    response_model=PolicyHistoryResponse,
    summary="Read a policy's full version history (operators only)",
    dependencies=[RateLimitDep],
)
def get_policy_history(
    policy_id: str,
    principal: PrincipalDep,
    container: ContainerDep,
) -> PolicyHistoryResponse:
    """Return every version, including drafts and retired ones.

    Gated even though it is a read: the history contains drafts whose rules are
    not in force, so an open read would disclose policy still under review.
    """
    history = container.get_policy_history().execute(principal, policy_id)
    return PolicyHistoryResponse.from_domain(history)
