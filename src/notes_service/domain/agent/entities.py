"""Agent aggregate: the service principal and its human-approval workflow.

The design rule this module enforces is that **an agent's authority is never
self-asserted**. An :class:`AgentIdentity` carries a capability ceiling, and a
:class:`ApprovalRequest` records that a *human* — not the agent — authorised a
sensitive action. Both are small, self-validating aggregates so a bug in a use
case cannot persist an escalation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from notes_service.domain.agent.value_objects import (
    DEFAULT_MAX_TOKEN_TTL_SECONDS,
    HARD_MAX_TOKEN_TTL_SECONDS,
    SENSITIVE_CAPABILITIES,
    AgentId,
    AgentStatus,
    ApprovalRequestId,
    ApprovalStatus,
    Capability,
    RuntimeIdentity,
    TokenAudience,
)
from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.identity.value_objects import UserId

_MAX_DISPLAY_NAME = 120

# An approval authorises one sensitive action, once, and not indefinitely.
# The security architecture caps approval validity and requires that an expired
# approval block execution (ENTERPRISE_SECURITY_ARCHITECTURE_V1 §11). Sensitive
# agent tools sit at the High tier, whose ceiling is 72 hours; this is the
# platform maximum, and tenant policy may only shorten it.
DEFAULT_APPROVAL_TTL = timedelta(hours=72)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Versioned:
    """Optimistic-concurrency mixin; every mutation bumps ``version``."""

    version: int
    updated_at: datetime

    def _bump(self, now: datetime | None) -> None:
        self.version += 1
        self.updated_at = now or _utcnow()


def _normalise_capabilities(
    capabilities: frozenset[Capability] | set[Capability],
) -> frozenset[Capability]:
    if not capabilities:
        raise ValidationError("an agent must have at least one capability")
    return frozenset(capabilities)


@dataclass(slots=True)
class AgentIdentity(_Versioned):
    """A registered service principal owned by a human user.

    ``capabilities`` is a *ceiling*, not a grant. Issuing a token narrows it
    further via :func:`~notes_service.domain.agent.delegation.attenuate`; the
    ceiling is what a delegator's request is validated against.
    """

    id: AgentId
    display_name: str
    owner_user_id: UserId
    runtime_identity: RuntimeIdentity
    capabilities: frozenset[Capability]
    status: AgentStatus = AgentStatus.ACTIVE
    max_token_ttl_seconds: int = DEFAULT_MAX_TOKEN_TTL_SECONDS
    # Suspension, reactivation, revocation and narrowing all change what this
    # principal may do, so each bumps ``version``. Persistence needs that to be
    # enforced, not decorative: ``save`` rewrites ``status``, ``capabilities``
    # and ``revoked_at`` from whatever the caller last read, so a stale copy
    # saved after a revoke restores ``active`` over ``revoked`` and clears
    # ``revoked_at`` — the emergency stop silently undone. A stale copy also
    # reinstates capabilities a narrowing removed.
    version: int = 1
    # The version this aggregate was last read from or written to storage at.
    # ``version - 1`` is not that value: a caller may mutate several times
    # between two saves, so persistence needs the exact stored version to build
    # its ``UPDATE ... WHERE version = :expected``.
    persisted_version: int = field(default=1, init=False, repr=False)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        self.persisted_version = self.version

    def mark_persisted(self) -> None:
        """Record that ``version`` is now what storage holds."""
        self.persisted_version = self.version

    @classmethod
    def register(
        cls,
        *,
        display_name: str,
        owner_user_id: UserId | str,
        runtime_identity: RuntimeIdentity | str,
        capabilities: frozenset[Capability] | set[Capability],
        agent_id: AgentId | None = None,
        max_token_ttl_seconds: int = DEFAULT_MAX_TOKEN_TTL_SECONDS,
    ) -> AgentIdentity:
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValidationError("agent display name must not be empty")
        trimmed = display_name.strip()
        if len(trimmed) > _MAX_DISPLAY_NAME:
            raise ValidationError(
                f"agent display name must be at most {_MAX_DISPLAY_NAME} characters"
            )
        if not 0 < max_token_ttl_seconds <= HARD_MAX_TOKEN_TTL_SECONDS:
            raise ValidationError(
                f"max token ttl must be between 1 and {HARD_MAX_TOKEN_TTL_SECONDS} seconds"
            )
        return cls(
            id=agent_id or AgentId.new(),
            display_name=trimmed,
            owner_user_id=UserId.parse(owner_user_id),
            runtime_identity=RuntimeIdentity(runtime_identity),
            capabilities=_normalise_capabilities(capabilities),
            max_token_ttl_seconds=max_token_ttl_seconds,
        )

    @property
    def is_operational(self) -> bool:
        return self.status.is_operational

    def assert_can_issue(self, *, audience: TokenAudience, ttl_seconds: int) -> None:
        """Fail closed unless this agent may be issued a token right now.

        Called on *every* issuance and on *every* token use (via the caller's
        resolution path), so revocation takes effect on the next request rather
        than at token expiry.
        """
        if not self.is_operational:
            raise InvariantViolation(
                f"agent {self.id} is {self.status.value} and cannot issue tokens"
            )
        if not isinstance(audience, TokenAudience):
            raise ValidationError(f"unknown token audience: {audience!r}")
        if ttl_seconds <= 0:
            raise ValidationError("token ttl must be positive")
        if ttl_seconds > min(self.max_token_ttl_seconds, HARD_MAX_TOKEN_TTL_SECONDS):
            raise ValidationError(
                f"token ttl {ttl_seconds}s exceeds the agent's maximum of "
                f"{min(self.max_token_ttl_seconds, HARD_MAX_TOKEN_TTL_SECONDS)}s"
            )

    def has_capability(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def suspend(self, *, now: datetime | None = None) -> None:
        if self.status is AgentStatus.REVOKED:
            raise InvariantViolation("a revoked agent cannot be suspended")
        self.status = AgentStatus.SUSPENDED
        self._bump(now)

    def activate(self, *, now: datetime | None = None) -> None:
        if self.status is AgentStatus.REVOKED:
            raise InvariantViolation("a revoked agent cannot be reactivated")
        self.status = AgentStatus.ACTIVE
        self._bump(now)

    def revoke(self, *, now: datetime | None = None) -> None:
        """Terminal emergency stop. Idempotent; a revoked agent stays revoked."""
        if self.status is AgentStatus.REVOKED:
            return
        self.status = AgentStatus.REVOKED
        self.revoked_at = now or _utcnow()
        self._bump(now)

    def narrow_capabilities(
        self, *, capabilities: frozenset[Capability] | set[Capability], now: datetime | None = None
    ) -> None:
        """Reduce the ceiling. Widening is rejected: an edit must not escalate."""
        requested = _normalise_capabilities(capabilities)
        added = requested - self.capabilities
        if added:
            raise InvariantViolation(
                "agent capabilities may only be narrowed; cannot add: "
                + ", ".join(sorted(c.value for c in added))
            )
        self.capabilities = requested
        self._bump(now)


@dataclass(slots=True)
class ApprovalRequest:
    """A human decision on one sensitive action an agent wants to take.

    The agent opens the request; only a distinct human may decide it. The
    separation-of-duties check lives here, in the aggregate, so no use case can
    forget it.
    """

    id: ApprovalRequestId
    agent_id: AgentId
    capability: Capability
    resource_ref: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = field(default_factory=_utcnow)
    decided_at: datetime | None = None
    decided_by_user_id: UserId | None = None
    reason: str | None = None
    expires_at: datetime | None = None
    # How many *distinct* humans must approve before this request authorises the
    # action. ``1`` is the ordinary case. A policy that imposes the
    # ``second-approver`` obligation opens the request with ``2`` so the
    # requirement is a property of the request itself rather than something the
    # gateway re-derives at spend time — a gateway that forgot to pass the number
    # would otherwise silently accept one signature for a two-person control.
    required_approvals: int = 1
    # The second signatory. Kept as its own field rather than a list because the
    # platform's maximum is two: one approver, and one distinct approver. A list
    # would imply a variable threshold this aggregate does not model, and a
    # variable threshold a caller can widen is not a control.
    second_approver_user_id: UserId | None = None
    second_decided_at: datetime | None = None
    second_reason: str | None = None
    # Who refused a request that already carried one approval. Kept apart from
    # ``decided_by_user_id`` so a denial after a partial approval does not erase
    # the record of who said yes: an auditor needs both the grant and the refusal
    # to understand how a two-person control actually behaved.
    denied_by_user_id: UserId | None = None
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by_user_id: UserId | None = None
    revoke_reason: str | None = None
    # Every decision changes whether this row authorises an action, so it needs
    # the same compare-and-set that notes, organisations and runs already have.
    # Without a version, ``save`` rewrites the whole row from whatever the caller
    # last read: a stale ``DecideApprovalRequest`` holding a pre-revocation copy
    # writes ``status=approved`` back over a ``revoked`` row, and a stale copy of
    # a spent approval clears ``consumed_at`` and makes it spendable again. Both
    # are silent re-grants of withdrawn authority, which is why this is a version
    # and not merely a status guard.
    version: int = 1
    # The version this aggregate was last read from or written to storage at.
    # ``version - 1`` is not that value: a caller may mutate several times
    # between two saves, so persistence needs the exact stored version to build
    # its ``UPDATE ... WHERE version = :expected``.
    persisted_version: int = field(default=1, init=False, repr=False)

    def __post_init__(self) -> None:
        self.persisted_version = self.version

    def mark_persisted(self) -> None:
        """Record that ``version`` is now what storage holds."""
        self.persisted_version = self.version

    def _bump(self) -> None:
        self.version += 1

    @classmethod
    def open(
        cls,
        *,
        agent: AgentIdentity,
        capability: Capability,
        resource_ref: str,
        approval_id: ApprovalRequestId | None = None,
        ttl: timedelta = DEFAULT_APPROVAL_TTL,
        now: datetime | None = None,
        required_approvals: int = 1,
    ) -> ApprovalRequest:
        if capability not in SENSITIVE_CAPABILITIES:
            raise InvariantViolation(
                f"capability {capability.value} is not sensitive and needs no approval"
            )
        if not agent.has_capability(capability):
            raise InvariantViolation(
                f"agent {agent.id} lacks capability {capability.value} and cannot request approval"
            )
        if not isinstance(resource_ref, str) or not resource_ref.strip():
            raise ValidationError("approval request must name a resource")
        if ttl <= timedelta(0):
            raise ValidationError("approval validity must be positive")
        if required_approvals not in (1, 2):
            # Two is the platform maximum. A larger number would need a list of
            # signatories and a threshold check this aggregate does not model,
            # and silently accepting it would record a requirement it cannot
            # enforce — the exact failure mode obligations exist to prevent.
            raise ValidationError("an approval requires one or two distinct approvers")
        opened_at = now or _utcnow()
        return cls(
            id=approval_id or ApprovalRequestId.new(),
            agent_id=agent.id,
            capability=capability,
            resource_ref=resource_ref.strip(),
            requested_at=opened_at,
            expires_at=opened_at + ttl,
            required_approvals=required_approvals,
        )

    @property
    def is_pending(self) -> bool:
        return self.status is ApprovalStatus.PENDING

    @property
    def approvals_recorded(self) -> int:
        """How many distinct humans have signed, saturating at the requirement."""
        recorded = 0
        if self.decided_by_user_id is not None:
            recorded += 1
        if self.second_approver_user_id is not None:
            recorded += 1
        return recorded

    @property
    def awaiting_second_approval(self) -> bool:
        """One signature is in, the request needs another, and nobody has denied it.

        This is a distinct state from "pending and untouched": an operator
        looking at the queue must be able to tell a request nobody has picked up
        from one that is half-approved and waiting on a second, different human.
        """
        return (
            self.status is ApprovalStatus.PENDING
            and self.required_approvals == 2
            and self.decided_by_user_id is not None
            and self.second_approver_user_id is None
        )

    @property
    def is_expired(self) -> bool:
        # Fail closed on a missing bound: an approval whose validity was never
        # recorded is not an approval with unlimited validity. Reading NULL as
        # "never expires" is how an unpersisted column silently becomes
        # permanent authority, so the absent case is treated as lapsed.
        return self.expired_at(now=_utcnow())

    def expired_at(self, *, now: datetime) -> bool:
        """Whether the validity bound has lapsed as of ``now``.

        Every caller that has a clock must use this rather than the
        ``is_expired`` property. The bound is stamped from the caller's clock
        when the request is opened, so judging it against the wall clock makes
        the two disagree whenever they differ (a frozen or injected clock, or
        simple skew): a lapsed approval can read as live, and a fresh one as
        expired. Comparing one instant against another is the only honest test.
        """
        return self.expires_at is None or now >= self.expires_at

    def is_usable(self, *, now: datetime | None = None) -> bool:
        """Whether this approval may authorise an action right now.

        Three independent conditions, all required: it was approved, nobody has
        spent it yet, and it has not expired. They are separate statuses in the
        security architecture (expired / revoked / invalidated all block
        execution), so they are checked separately here rather than collapsed
        into one boolean that a caller could misread.

        A missing ``expires_at`` counts as expired, not as unbounded. The column
        is nullable only for rows written before it existed, and the security
        architecture requires those to become unusable rather than retain
        indefinite authority.
        """
        if self.status is not ApprovalStatus.APPROVED:
            return False
        # Belt to the status check's braces for the two-person control: a row
        # whose status reads approved but that carries fewer distinct signatures
        # than it requires must not authorise anything. `approve` never produces
        # that state, but a contradictory row (a hand-written UPDATE, a partial
        # migration, a future writer that sets status directly) must fail closed
        # rather than spend a control it never satisfied.
        if self.approvals_recorded < self.required_approvals:
            return False
        if self.consumed_at is not None:
            return False
        # Belt to the status check's braces: a row that carries a revocation
        # stamp is not usable even if its status column still reads approved.
        # The gateway must fail closed on contradictory rows, not trust the
        # column that happens to permit execution.
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return False
        moment = now or _utcnow()
        return moment < self.expires_at

    def _assert_decidable(self, approver_user_id: UserId | None, *, now: datetime) -> UserId:
        if approver_user_id is None:
            raise InvariantViolation(
                "an agent cannot approve its own action; a human approver is required"
            )
        if self.status is not ApprovalStatus.PENDING:
            raise InvariantViolation(f"approval request is already {self.status.value}")
        if self.expired_at(now=now):
            # Deciding an expired request would produce an APPROVED row that the
            # gateway must then refuse; refuse the decision instead, so the
            # recorded status never claims an authority that has lapsed.
            raise InvariantViolation("approval request has expired and cannot be decided")
        return approver_user_id

    def approve(
        self,
        *,
        approver_user_id: UserId | str | None,
        now: datetime | None = None,
        reason: str | None = None,
    ) -> None:
        """Record one human's approval, granting the action only when complete.

        A request that needs two approvers is *not* approved by the first
        signature. The first signature is recorded and the status stays
        ``PENDING`` until a second, **distinct** human signs. Marking it
        ``APPROVED`` on one signature would make the requirement advisory: the
        gateway reads ``is_usable``, which reads ``status``, so a premature
        ``APPROVED`` is a live grant of the two-person control's authority to one
        person.

        The distinctness check is here, in the aggregate, not in the use case:
        every path that decides an approval goes through this method, and the
        only defence that cannot be forgotten is the one at the data.
        """
        approver = UserId.parse(approver_user_id) if approver_user_id is not None else None
        decided_at = now or _utcnow()
        # A second signature is a *new* decision on a request that already has
        # one, so the ordinary "only a pending request is decidable" guard does
        # not apply to it. Route it before that guard runs.
        if self.required_approvals == 2 and self.decided_by_user_id is not None:
            self._approve_as_second(approver, now=decided_at, reason=reason)
            return
        self._assert_decidable(approver, now=decided_at)
        assert approver is not None  # _assert_decidable guarantees it
        self.decided_by_user_id = approver
        self.decided_at = decided_at
        self.reason = reason
        if self.required_approvals == 1:
            self.status = ApprovalStatus.APPROVED
        # With two required the status stays PENDING: one signature is a partial
        # approval, not an approval.
        self._bump()

    def _approve_as_second(
        self, approver: UserId | None, *, now: datetime, reason: str | None
    ) -> None:
        if approver is None:
            raise InvariantViolation(
                "an agent cannot approve its own action; a human approver is required"
            )
        if self.status is not ApprovalStatus.PENDING:
            raise InvariantViolation(f"approval request is already {self.status.value}")
        if self.expired_at(now=now):
            raise InvariantViolation("approval request has expired and cannot be decided")
        if self.second_approver_user_id is not None:
            raise InvariantViolation("approval request already has its second approval")
        if approver == self.decided_by_user_id:
            # Separation of duties: a second click by the first approver is not a
            # second approver. Reusing them would make the whole obligation a
            # formality that reads as a control in the audit trail.
            raise InvariantViolation(
                "the second approval must come from a different human than the first"
            )
        self.second_approver_user_id = approver
        self.second_decided_at = now
        self.second_reason = reason
        self.status = ApprovalStatus.APPROVED
        self._bump()

    def deny(
        self,
        *,
        approver_user_id: UserId | str | None,
        now: datetime | None = None,
        reason: str | None = None,
    ) -> None:
        """Refuse the action. One refusal ends a two-person control.

        A request half-approved by one human and then refused by a second must
        not keep the first signature as though it were still live: the refusal
        wins, the status becomes ``DENIED``, and the first approver's grant stays
        visible in ``decided_by_user_id`` rather than being overwritten. Writing
        the refuser over it would destroy the record of a grant that a second
        person then blocked — the exact sequence an auditor needs to see.
        """
        approver = UserId.parse(approver_user_id) if approver_user_id is not None else None
        decided_at = now or _utcnow()
        if self.required_approvals == 2 and self.decided_by_user_id is not None:
            if approver is None:
                raise InvariantViolation(
                    "an agent cannot deny its own action; a human approver is required"
                )
            if self.status is not ApprovalStatus.PENDING:
                raise InvariantViolation(f"approval request is already {self.status.value}")
            if self.expired_at(now=decided_at):
                raise InvariantViolation("approval request has expired and cannot be decided")
            self.denied_by_user_id = approver
            self.second_decided_at = decided_at
            self.second_reason = reason
            self.status = ApprovalStatus.DENIED
            self._bump()
            return
        self._assert_decidable(approver, now=decided_at)
        assert approver is not None  # _assert_decidable guarantees it
        self.status = ApprovalStatus.DENIED
        self.decided_by_user_id = approver
        self.decided_at = decided_at
        self.reason = reason
        self._bump()

    def raise_required_approvals(self, required: int) -> None:
        """Raise the number of distinct signatures this request needs, never lower it.

        A published policy can tighten while a request is still open. Reusing the
        open request is an efficiency, and it must not become a way to spend
        authority under a rule that no longer applies: an approved-by-one request
        that a stricter policy now demands two signatures for must drop back to
        pending. Only a request nobody has signed is raised — one already carrying
        a signature cannot be un-approved silently, so it is refused and the
        caller opens a fresh request under the current policy.

        Lowering is refused outright: a policy that relaxed is a reason to open a
        new request, not to rewrite a decision already recorded at the stricter
        threshold.
        """
        if required not in (1, 2):
            raise ValidationError("an approval requires one or two distinct approvers")
        if required < self.required_approvals:
            raise InvariantViolation("an approval requirement may only be raised, never lowered")
        if required == self.required_approvals:
            return
        if self.decided_by_user_id is not None or self.second_approver_user_id is not None:
            raise InvariantViolation(
                "cannot raise the requirement of a request that already carries a signature"
            )
        self.required_approvals = required
        self._bump()

    def revoke(
        self,
        *,
        revoked_by_user_id: UserId | str | None,
        now: datetime | None = None,
        reason: str | None = None,
    ) -> None:
        """Withdraw an approval that has already been granted.

        An approved-but-unspent request is live authority: the gateway will
        honour it on the next matching call. Until this existed, the only way to
        stop it was to wait for expiry or spend it, so a human who approved by
        mistake had no way to take it back. Revocation is that way.

        Only an approved request can be revoked. A pending one is denied
        instead, and an already-consumed one has already authorised its action —
        revoking it would rewrite history rather than prevent an act, so it is
        refused rather than silently no-op'd.

        Separation of duties is enforced here for the same reason it is on
        ``approve``: an agent has no user id, so an agent cannot withdraw the
        consent it asked for.
        """
        revoker = UserId.parse(revoked_by_user_id) if revoked_by_user_id is not None else None
        if revoker is None:
            raise InvariantViolation(
                "an agent cannot revoke its own approval; a human approver is required"
            )
        if self.status is not ApprovalStatus.APPROVED:
            raise InvariantViolation(
                f"only an approved request can be revoked; this one is {self.status.value}"
            )
        if self.consumed_at is not None:
            raise InvariantViolation(
                "approval request has already been spent and cannot be revoked"
            )
        self.status = ApprovalStatus.REVOKED
        self.revoked_at = now or _utcnow()
        self.revoked_by_user_id = revoker
        # Keep the approver's original justification intact and record the
        # withdrawal separately: overwriting ``reason`` would erase why the grant
        # was made, which is exactly what an auditor needs to compare against why
        # it was taken back.
        self.revoke_reason = reason
        self._bump()

    def consume(self, *, now: datetime | None = None) -> None:
        """Spend this approval on exactly one action.

        An approval is an authorisation for one sensitive act, not a standing
        permission. Without this, a single human decision to delete one note
        would silently authorise every later delete of that note for as long as
        the request row lived. Consuming stamps ``consumed_at`` so the gateway's
        ``is_usable`` check fails for every subsequent attempt.
        """
        if not self.is_usable(now=now):
            raise InvariantViolation(
                f"approval request {self.id} is not usable and cannot be consumed"
            )
        self.consumed_at = now or _utcnow()
        self._bump()
