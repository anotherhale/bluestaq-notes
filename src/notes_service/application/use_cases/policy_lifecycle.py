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

Approve is its own verb (NEX-065)
---------------------------------

The full lifecycle is ``Draft → Validate → Impact Preview → Approve → Publish →
Monitor``. NEX-050 built the control approval depends on — **separation of duties**,
enforced in the domain and again here — but a control is not a record. This module
now also ships the missing ``Approve`` verb: :class:`ApproveDraft` transitions a
draft to ``APPROVED`` with its own operator scope, and :class:`PublishDraft` refuses
anything that has not been approved. Putting a policy in force is therefore three
recorded decisions by three different principals rather than one: author, approver
and publisher are pairwise distinct (NEX-078), so no single operator can perform
two of the three roles.

The approval record is deliberately part of the *version*, not a separate table,
because it describes that version's own history: who approved v3, and when, must
survive v3 being retired by v4. A rollback re-drafts already-reviewed rules, so it
inherits the source version's approval record verbatim instead of manufacturing a
new one.

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
from datetime import datetime

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
    approval_of_duties,
    deployment_obligation_gaps,
    new_policy_version,
    separation_of_duties,
    supersedes,
)
from notes_service.domain.identity.principal import Principal

DRAFT_OPERATION = "policy-draft"
"""Operator scope for drafting a policy version. Names the operation, not a role."""
PUBLISH_OPERATION = "policy-publish"
"""Operator scope for publishing a policy version, which puts it in force."""
APPROVE_OPERATION = "policy-approve"
"""Operator scope for approving a policy version (NEX-065).

Separate from ``policy-publish`` on purpose. If approval and publication shared a
scope, an operator granted that scope could satisfy the whole two-person control
alone — which is the control, not a permission to tidy up. A deployment that wants
a genuine review must grant ``policy-approve`` to a different operator than
``policy-publish``.
"""
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
class ApprovePolicyVersion:
    """A request to approve a specific draft, making it publishable (NEX-065).

    It carries no approver. ``approved_by`` is stamped from the authenticated
    principal inside :class:`ApproveDraft`, for the same reason the publisher is:
    an approval recorded against a caller-supplied name would not be evidence that
    anyone reviewed the policy.
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


def _require_distinct_roles(draft: PolicyVersion, *, publisher: str) -> None:
    """Turn the domain separation-of-duties rule into an application ``Conflict``.

    The domain raises :class:`InvariantViolation`, which the transport maps to a
    generic 422 "the operation would break an invariant". For a caller who tried to
    publish their own draft the honest answer is a 409 with the reason, so they can
    ask a second operator to publish it. The domain check stays as the backstop for
    callers that bypass this use case; this function only re-expresses the same rule
    with a more useful status.

    Three identities must be distinct (NEX-078), not two: the author, the approver
    and the publisher. Passing ``draft.approved_by`` through to the domain is what
    closes the gap where an approver could also publish, completing both halves of
    the control alone.

    A ``None`` author or approver is a conflict too, not a pass: the domain refuses
    it, and translating that into "go ahead" here would undo the control for exactly
    the drafts that never recorded who wrote or reviewed them.
    """
    try:
        separation_of_duties(
            author=draft.drafted_by,
            approver=draft.approved_by,
            publisher=publisher,
            ref=draft.ref,
        )
    except InvariantViolation as exc:
        raise Conflict(str(exc)) from exc


def _require_distinct_approver(draft: PolicyVersion, *, approver: str) -> None:
    """The same two-person rule for the approval step (NEX-065).

    Kept separate from :func:`_require_distinct_roles` rather than folded into it
    because the two check different pairs. An author who approves their own draft
    and has it published by a third operator satisfies the publish-time check
    perfectly while still never having had their rules reviewed, so the approval
    step needs its own refusal.
    """
    try:
        approval_of_duties(author=draft.drafted_by, approver=approver, ref=draft.ref)
    except InvariantViolation as exc:
        raise Conflict(str(exc)) from exc


def _require_newer_release(draft: PolicyVersion, *, in_force: PolicyVersion | None) -> None:
    """Turn the domain's "must outrank what is in force" rule into a ``Conflict``.

    Same translation the two separation-of-duties helpers do: the domain raises
    :class:`InvariantViolation`, and the honest answer to a caller who tried to
    publish an older approved version is a 409 that says so, not a generic 422.
    The domain check stays as the backstop for callers that bypass this use case.

    This closes a fail-open, not a tidy-up. ``PublishDraft`` previously retired
    whatever was in force and published whatever it was handed, so re-publishing a
    superseded-but-still-``APPROVED`` version silently reverted every tightening
    made since — and did it under ``policy-publish`` alone, skipping the
    ``policy-rollback`` scope and its separation-of-duties re-check.
    """
    try:
        supersedes(draft, in_force)
    except InvariantViolation as exc:
        raise Conflict(str(exc)) from exc


class ApproveDraft:
    """Approve a stored draft so that a second principal may publish it (NEX-065).

    Approval is deliberately its own use case with its own operator scope, not a
    flag on the publish request. A publish that carried "and also approve it" would
    let one operator with one scope complete both halves of the two-person control
    in a single call, which is the control being bypassed rather than exercised.
    """

    def __init__(
        self, *, store: PolicyVersionRepository, clock: Clock, operator_gate: OperatorGate
    ) -> None:
        self._store = store
        self._clock = clock
        self._operator_gate = operator_gate

    def execute(self, principal: Principal, request: ApprovePolicyVersion) -> PolicyVersion:
        self._operator_gate.authorize(principal, operation=APPROVE_OPERATION)
        draft = self._store.get(request.policy_id, request.version)
        if draft is None:
            raise ResourceNotFound(
                f"policy version {request.policy_id}@v{request.version} not found"
            )
        if draft.status is not PolicyStatus.DRAFT:
            # An approved version already names its approver; re-approving would
            # overwrite that record. A published or retired version is past the
            # point where approval means anything.
            raise Conflict(
                f"policy version {draft.ref} is {draft.status.value}, and only a draft "
                "can be approved"
            )
        approver = actor_label(principal)
        _require_distinct_approver(draft, approver=approver)
        approved = draft.approve(now=self._clock.now(), approved_by=approver)
        self._store.approve(approved)
        return approved


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
        if draft.status is PolicyStatus.DRAFT:
            # The gap NEX-065 closes. A draft that reaches publish skipped the
            # approval record, so the honest answer is "approve it first" — a 409
            # the caller can act on, not a 422 invariant error.
            raise Conflict(
                f"policy version {draft.ref} is a draft, and must be approved before it "
                "can be published"
            )
        if draft.status is not PolicyStatus.APPROVED:
            # Publishing a published or retired version would rewrite history.
            # It is a conflict with the version's state, not a missing resource.
            raise Conflict(
                f"policy version {draft.ref} is {draft.status.value} and cannot be published"
            )
        publisher = actor_label(principal)
        _require_distinct_roles(draft, publisher=publisher)
        current = self._store.published_for(request.policy_id)
        _require_newer_release(draft, in_force=current)
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
        # A version in force from before NEX-065 has no approval to inherit. There is
        # nothing honest to copy, and inventing one would put rules back in force
        # under a review that never happened, so the rollback is refused before any
        # draft is written and the caller is told the one thing that will work:
        # re-draft with a named approver.
        if not source.has_approval_record:
            raise Conflict(
                f"cannot roll back to {source.ref}: it is in force without a recorded"
                " approval, so there is no review to inherit. Re-draft its rules with a"
                " named approver instead."
            )
        # A rollback authors nothing: it restores rules that already went through
        # review, so the version's *author* stays the person who wrote those rules
        # and the caller performing the rollback is the *publisher*. Separation of
        # duties therefore reads the same way it does everywhere else — the author
        # of the rules must not be the principal putting them back in force — and
        # an author cannot silently re-force their own retired rules. Both
        # identities are still recorded, so the history names who restored what.
        publisher = actor_label(principal)
        _require_distinct_roles(source, publisher=publisher)
        author = source.drafted_by
        assert author is not None  # guaranteed by _require_distinct_roles
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
        # The restored rules already carry an approval: they were reviewed when the
        # source version was approved, and a rollback changes which rules are in
        # force, not what they say. So the new version inherits that approval record
        # verbatim — same approver, same timestamp — instead of demanding a fresh
        # review or, worse, stamping a new approval nobody gave. ``approve`` still
        # re-checks separation of duties, so a source version that somehow recorded
        # a self-approval cannot be laundered through a rollback.
        assert source.approved_at is not None  # narrowed by has_approval_record
        assert source.approved_by is not None
        approved = draft.approve(now=source.approved_at, approved_by=source.approved_by)
        self._store.approve(approved)
        published = approved.publish(now=self._clock.now(), published_by=publisher)
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


@dataclass(frozen=True, slots=True)
class PolicyInForce:
    """One policy version currently in force, and what this deployment can enforce.

    ``unenforceable_obligations`` is the operational half. The authoring path
    refuses to draft a rule naming an obligation the deployment has no adapter for
    (:func:`_require_enforceable_obligations`), but that guard runs once, at author
    time. A version drafted while a limiter was configured keeps its ``rate-limit``
    rule when the limiter is later removed, and from that moment every call the
    rule matches is denied with the reason visible only in the audit trail. Naming
    the gap here makes it visible to the same operator who can publish a fix.

    It is computed with the gateway's own
    :func:`~notes_service.domain.governance.policy.deployment_obligation_gaps`, so
    this report and the enforcement path cannot disagree about what is supported.
    """

    policy_id: str
    version: int
    rules: tuple[PolicyRule, ...]
    rationale: str
    published_at: datetime | None
    published_by: str | None
    drafted_by: str | None
    approved_by: str | None
    approval_on_record: bool
    unenforceable_obligations: tuple[str, ...]

    @property
    def ref(self) -> str:
        return f"{self.policy_id}@v{self.version}"


@dataclass(frozen=True, slots=True)
class PoliciesInForce:
    """Every policy version in force at one instant, in policy-id order.

    This is the set the enforcement gateway evaluates, reported as data. Without
    it the only way to ask "what is restricting this deployment right now" was to
    already know every ``policy_id`` and read each history in turn — and an id is
    free-form text an operator cannot enumerate from any other endpoint. A
    deployment whose policy set is unenumerable cannot be audited, only sampled.

    ``policies`` empty is a real, actionable answer rather than an error: nothing is
    published means :func:`~notes_service.domain.governance.policy.evaluate` has
    nothing to allow, and a deployment that denies everything is a fact an operator
    needs told. So an empty result is ``200`` with an empty list, never ``404``.

    ``as_of`` is the gate's clock, taken after authorisation, and ``source_name``
    names the adapter that answered, so a reader cannot mistake the process-local
    store for a durable deployment-wide one.
    """

    policies: tuple[PolicyInForce, ...]
    as_of: datetime
    source_name: str

    @property
    def policy_count(self) -> int:
        return len(self.policies)


class ListPoliciesInForce:
    """Return every policy version currently in force to an authorized operator.

    Gated by ``policy-history``, the scope that already reads a policy's full
    version history. A history discloses strictly *more* than this report — it
    includes drafts whose rules are not yet in force and versions already retired —
    so reuse here cannot escalate: anything an ``in-force`` reader sees, a history
    reader already sees. Minting a fifth policy token would instead force every
    auditor to be granted twice to answer one question, the same reasoning
    NEX-080 used to reuse ``operator-access-report`` for revoked grants.

    A corrupt stored row makes this raise rather than skip. The store re-validates
    on read and raises, which is deliberate: silently omitting an unparseable
    policy from "what is in force" would understate the restriction set, and a
    report that quietly drops rows is worse than one that fails loudly.
    """

    def __init__(
        self,
        *,
        store: PolicyVersionRepository,
        operator_gate: OperatorGate,
        clock: Clock,
        rate_limiter_configured: bool,
    ) -> None:
        self._store = store
        self._operator_gate = operator_gate
        self._clock = clock
        self._rate_limiter_configured = rate_limiter_configured

    def execute(self, principal: Principal) -> PoliciesInForce:
        # Gate first, never after: a refused caller must not learn how many
        # policies constrain this deployment, and the gate records the attempt.
        self._operator_gate.authorize(principal, operation=HISTORY_OPERATION)

        published = self._store.all_published()
        return PoliciesInForce(
            policies=tuple(self._describe(version) for version in published),
            as_of=self._clock.now(),
            source_name=self._store.name,
        )

    def _describe(self, version: PolicyVersion) -> PolicyInForce:
        obligations = tuple(
            dict.fromkeys(obligation for rule in version.rules for obligation in rule.obligations)
        )
        gaps = deployment_obligation_gaps(
            obligations, rate_limiter_configured=self._rate_limiter_configured
        )
        return PolicyInForce(
            policy_id=version.policy_id,
            version=version.version,
            rules=version.rules,
            rationale=version.rationale,
            published_at=version.published_at,
            published_by=version.published_by,
            drafted_by=version.drafted_by,
            approved_by=version.approved_by,
            approval_on_record=version.has_approval_record,
            unenforceable_obligations=tuple(obligation.value for obligation in gaps),
        )


__all__ = [
    "ApproveDraft",
    "ApprovePolicyVersion",
    "DraftPolicy",
    "DraftPolicyVersion",
    "GetPolicyHistory",
    "ListPoliciesInForce",
    "PoliciesInForce",
    "PolicyHistory",
    "PolicyInForce",
    "PublishDraft",
    "PublishPolicyVersion",
    "RollbackPolicy",
    "RollbackPolicyVersion",
]
