"""Publish a governance policy version: draft, validate, publish, retire (NEX-047).

The security architecture's policy lifecycle is
``Draft → Validate/Test → Impact Preview → Approve → Publish → Monitor``, with the
hard rule that **published releases are immutable** and a rollback is a new
release. This use case implements the part of that lifecycle the domain cannot
enforce on its own, because it needs a store and a clock:

* **drafting** assigns the next version number from the store, never from the
  caller, so two authors cannot both claim v4;
* **publishing** stamps the clock and the publisher and retires whatever was
  published before, so exactly one version per policy id is ever in force;
* **rollback** is modelled as *re-drafting an old version's rules as a new
  version* — there is deliberately no "un-publish" path, because un-publishing
  would delete history, and history is what an audit of a past decision needs.

Approve is not here yet, and that is honest
-------------------------------------------

The full lifecycle includes an approval step. This slice ships draft → publish →
retire because those are the transitions the *store* must support. The approval
*decision* — a separate reviewer signing off before publication — is still not
built. What is built, from NEX-050, is the control that approval depends on:
**separation of duties**. The principal that authored a version cannot be the
principal that publishes it, enforced in the domain and again here. That is a
real, tested restriction on who can put a policy in force, not a placeholder gate
that always passes. A distinct approval *record* on top of it (an approve verb
with its own scope, obligations and audit entry) remains the follow-on slice.

Why a use case and not a repository method
------------------------------------------

Publication has rules that span calls: the new version must outrank the current
one, the previous version must be retired in the same operation, and the whole
thing must be idempotent enough that a retried publish does not produce two
published versions. Those are application concerns. The repository stores; this
decides.
"""

from __future__ import annotations

from dataclasses import dataclass

from notes_service.application.actor_label import actor_label
from notes_service.application.errors import Conflict, ResourceNotFound
from notes_service.application.ports.clock import Clock
from notes_service.application.ports.policy_versions import PolicyVersionRepository
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.governance.policy import (
    PolicyRule,
    PolicyStatus,
    PolicyVersion,
    deployment_obligation_gaps,
    new_policy_version,
    separation_of_duties,
)
from notes_service.domain.identity.principal import Principal

DRAFT_OPERATION = "policy-draft"
"""Operator scope for drafting a policy version. Names the operation, not a role."""
PUBLISH_OPERATION = "policy-publish"
"""Operator scope for publishing a policy version, which puts it in force."""
ROLLBACK_OPERATION = "policy-rollback"
"""Operator scope for rolling a policy back to an earlier release."""
HISTORY_OPERATION = "policy-history"
"""Operator scope for reading a policy's version history."""


@dataclass(frozen=True, slots=True)
class DraftPolicyVersion:
    """A request to draft the next version of a policy.

    It carries no author: the author is stamped from the authenticated principal
    inside :class:`DraftPolicy`, so a request body cannot attribute a draft to
    someone else — the same rule the publisher follows, for the same reason.
    """

    policy_id: str
    rules: tuple[PolicyRule, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class PublishPolicyVersion:
    """A request to publish a specific draft.

    It carries no publisher. ``published_by`` is stamped from the authenticated
    principal inside :class:`PublishDraft`, never read from a request body: a
    caller-chosen publisher would let anyone attribute a policy in force to
    someone else, and separation of duties is only meaningful if both identities
    are authoritative.
    """

    policy_id: str
    version: int


@dataclass(frozen=True, slots=True)
class RollbackPolicyVersion:
    """A request to restore an earlier version's rules as a *new* version.

    Rollback is not a mutation of history: it reads the named version's rules and
    drafts them under the next version number, so the fact that a rollback
    happened is itself recorded.

    No publisher is carried, for the same reason :class:`PublishPolicyVersion`
    carries none. A rollback is a publish, so the caller who rolls back is the
    publisher, and separation of duties applies to it: the principal that drafts
    the rolled-back version must not be the one who puts it in force.
    """

    policy_id: str
    to_version: int
    rationale: str


class DraftPolicy:
    """Create a draft version of a policy, or of a new policy."""

    def __init__(
        self,
        *,
        store: PolicyVersionRepository,
        clock: Clock,
        operator_gate: OperatorGate,
        rate_limiter_configured: bool = True,
    ) -> None:
        self._store = store
        self._clock = clock
        self._operator_gate = operator_gate
        self._rate_limiter_configured = rate_limiter_configured

    def execute(self, principal: Principal, request: DraftPolicyVersion) -> PolicyVersion:
        # Gate first, mutate second: a refused caller must not reach the store,
        # and the gate records the granted attempt before anything is written.
        self._operator_gate.authorize(principal, operation=DRAFT_OPERATION)
        if not request.rules:
            # A policy with no rules can only ever deny, which is a valid but
            # almost always unintended shape. Refusing here turns a silent
            # deny-everything into a loud request error.
            raise ValidationError("a policy version must have at least one rule")
        _require_enforceable_obligations(
            request.rules, rate_limiter_configured=self._rate_limiter_configured
        )
        version_number = self._store.next_version_number(request.policy_id)
        draft = new_policy_version(
            policy_id=request.policy_id,
            version=version_number,
            rules=request.rules,
            rationale=request.rationale,
            drafted_by=actor_label(principal),
            created_at=self._clock.now(),
        )
        self._store.add_draft(draft)
        return draft


def _require_enforceable_obligations(
    rules: tuple[PolicyRule, ...], *, rate_limiter_configured: bool
) -> None:
    """Refuse to draft a rule whose obligation this deployment cannot enforce.

    The gateway already refuses at *call* time when an allow rule carries an
    obligation it cannot satisfy, and that refusal is the real control — a policy
    can never widen authority by naming an obligation nobody enforces. But a
    policy that publishes cleanly and then denies every call it matches is a trap
    for its author: the failure surfaces as unexplained denials in the audit trail
    long after the version went into force.

    Catching it here does not weaken anything. It is the same judgement, asked one
    step earlier, against the same function the gateway uses, so the two cannot
    disagree about what the deployment supports. A draft is still unpublished and
    mutable, so refusing it costs the author nothing but a correction.

    Only deployment-wide gaps are decidable at this point. Whether a particular
    tool is human-gated, or whether a delegation meets a ``step-up`` floor,
    depends on the tool and the caller, and stays a call-time refusal.
    """
    obligations = tuple(
        dict.fromkeys(obligation for rule in rules for obligation in rule.obligations)
    )
    gaps = deployment_obligation_gaps(obligations, rate_limiter_configured=rate_limiter_configured)
    if not gaps:
        return
    detail = "; ".join(f"{obligation.value}: {reason}" for obligation, reason in gaps.items())
    raise ValidationError(
        f"policy version names obligation(s) this deployment cannot enforce ({detail}); "
        "publishing it would deny every call it matches"
    )


def _require_two_principals(draft: PolicyVersion, *, publisher: str) -> None:
    """Turn the domain separation-of-duties rule into an application ``Conflict``.

    The domain raises :class:`InvariantViolation`, which the transport maps to a
    generic 422 "the operation would break an invariant". For a caller who tried to
    publish their own draft the honest answer is a 409 with the reason, so they can
    ask a second operator to publish it. The domain check stays as the backstop for
    callers that bypass this use case; this function only re-expresses the same rule
    with a more useful status.

    A ``None`` author is a conflict too, not a pass: the domain refuses it, and
    translating that into "go ahead" here would undo the control for exactly the
    drafts that never recorded an author.
    """
    try:
        separation_of_duties(author=draft.drafted_by, publisher=publisher, ref=draft.ref)
    except InvariantViolation as exc:
        raise Conflict(str(exc)) from exc


class PublishDraft:
    """Publish a stored draft, retiring whatever was in force before it."""

    def __init__(
        self, *, store: PolicyVersionRepository, clock: Clock, operator_gate: OperatorGate
    ) -> None:
        self._store = store
        self._clock = clock
        self._operator_gate = operator_gate

    def execute(self, principal: Principal, request: PublishPolicyVersion) -> PolicyVersion:
        self._operator_gate.authorize(principal, operation=PUBLISH_OPERATION)
        draft = self._store.get(request.policy_id, request.version)
        if draft is None:
            raise ResourceNotFound(
                f"policy version {request.policy_id}@v{request.version} not found"
            )
        if draft.status is not PolicyStatus.DRAFT:
            # Publishing a published or retired version would rewrite history.
            # It is a conflict with the version's state, not a missing resource.
            raise Conflict(
                f"policy version {draft.ref} is {draft.status.value} and cannot be published"
            )
        publisher = actor_label(principal)
        _require_two_principals(draft, publisher=publisher)
        current = self._store.published_for(request.policy_id)
        published = draft.publish(now=self._clock.now(), published_by=publisher)
        # Retire the outgoing version *before* publishing the new one. If the
        # process dies between the two writes, the store is briefly left with the
        # old version retired and nothing published — deny, not a wrong allow —
        # and a retry completes. The reverse order would leave two published
        # versions, which the store refuses.
        if current is not None:
            self._store.retire(current.retire())
        self._store.publish(published)
        return published


class RollbackPolicy:
    """Restore an earlier version's rules as a new, higher version number."""

    def __init__(
        self, *, store: PolicyVersionRepository, clock: Clock, operator_gate: OperatorGate
    ) -> None:
        self._store = store
        self._clock = clock
        self._operator_gate = operator_gate

    def execute(self, principal: Principal, request: RollbackPolicyVersion) -> PolicyVersion:
        self._operator_gate.authorize(principal, operation=ROLLBACK_OPERATION)
        source = self._store.get(request.policy_id, request.to_version)
        if source is None:
            raise ResourceNotFound(
                f"policy version {request.policy_id}@v{request.to_version} not found"
            )
        if source.status is PolicyStatus.DRAFT:
            # Rolling back to a draft would publish something nobody approved.
            raise Conflict(
                f"cannot roll back to {source.ref}: it is a draft, not a released version"
            )
        # A rollback authors nothing: it restores rules that already went through
        # review, so the version's *author* stays the person who wrote those rules
        # and the caller performing the rollback is the *publisher*. Separation of
        # duties therefore reads the same way it does everywhere else — the author
        # of the rules must not be the principal putting them back in force — and
        # an author cannot silently re-force their own retired rules. Both
        # identities are still recorded, so the history names who restored what.
        publisher = actor_label(principal)
        _require_two_principals(source, publisher=publisher)
        author = source.drafted_by
        assert author is not None  # guaranteed by _require_two_principals
        version_number = self._store.next_version_number(request.policy_id)
        draft = new_policy_version(
            policy_id=request.policy_id,
            version=version_number,
            rules=source.rules,
            rationale=request.rationale,
            drafted_by=author,
            created_at=self._clock.now(),
        )
        self._store.add_draft(draft)
        published = draft.publish(now=self._clock.now(), published_by=publisher)
        current = self._store.published_for(request.policy_id)
        if current is not None:
            self._store.retire(current.retire())
        self._store.publish(published)
        return published


@dataclass(frozen=True, slots=True)
class PolicyHistory:
    """The full version history of one policy, oldest first."""

    policy_id: str
    versions: tuple[PolicyVersion, ...]

    @property
    def published(self) -> PolicyVersion | None:
        matches = [v for v in self.versions if v.status is PolicyStatus.PUBLISHED]
        return matches[0] if matches else None


class GetPolicyHistory:
    """Read a policy's version history, including drafts and retired versions."""

    def __init__(self, *, store: PolicyVersionRepository, operator_gate: OperatorGate) -> None:
        self._store = store
        self._operator_gate = operator_gate

    def execute(self, principal: Principal, policy_id: str) -> PolicyHistory:
        # Even the read is gated: a history contains drafts whose rules are not
        # yet in force, so an ungated read would disclose policy under review.
        self._operator_gate.authorize(principal, operation=HISTORY_OPERATION)
        versions = self._store.history(policy_id)
        if not versions:
            raise ResourceNotFound(f"policy {policy_id!r} has no versions")
        return PolicyHistory(policy_id=policy_id, versions=versions)


__all__ = [
    "DraftPolicy",
    "DraftPolicyVersion",
    "GetPolicyHistory",
    "PolicyHistory",
    "PublishDraft",
    "PublishPolicyVersion",
    "RollbackPolicy",
    "RollbackPolicyVersion",
]
