"""Read back who currently holds an operator grant, and until when (NEX-045).

NEX-044 made each grant narrow, but the allowlist was still a static string. It
could answer "is this caller allowed" and nothing answered **who is an operator
right now, since when, and until when**. That is the question an audit of a
privileged surface must be able to answer about itself, and a config value
structurally cannot: it has no holders, only a set of strings.

This is the read side of the stored grant. It answers:

* **who** — every user holding at least one active scope at the caller's clock;
* **what** — which operations each may use;
* **until when** — the earliest ``expires_at`` among a holder's active grants, or
  ``None`` when a holder has an unbounded grant. An unbounded grant is the
  strongest thing a holder has, so it is what the summary reports: reporting the
  nearest deadline when one grant never lapses would understate the exposure.

It is gated by the same
:class:`~notes_service.application.use_cases.operator_gate.OperatorGate` as every
other operational view, and fails closed the same way: an empty store admits
nobody, and a non-operator gets ``404`` rather than ``403``. A granted read of
*this* view is itself recorded, so enumerating the holders is auditable too.

Expiry is evaluated at the gate's clock
---------------------------------------

``now`` is read once from the gate's clock and threaded through every filter, so
the set of holders this view reports is the set the gate would admit at the same
instant. Reading the clock twice would let a grant expire between the gate's
check and this report — the R29 disagreement in miniature.

It takes no unit of work: neither reading nor issuing a grant touches content,
and the grant store owns its own session (see the adapter).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from notes_service.application.errors import Conflict
from notes_service.application.ports.clock import Clock
from notes_service.application.ports.operator_grants import OperatorGrantRepository
from notes_service.application.use_cases.operator_gate import WILDCARD, OperatorGate
from notes_service.domain.errors import ValidationError
from notes_service.domain.identity.operator_grant import OperatorGrant, OperatorGrantRationale
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import UserId

OPERATION = "operator-access-report"
"""The scope that gates reading the holder list.

Reusing the existing operator-access scope rather than minting a new one keeps
the number of platform-wide scopes small: whoever may read the access log is
already trusted with the most sensitive operational view, and "who holds a
grant" is the same class of question. A deployment that wants to separate them
can add a distinct scope later without changing this use case's shape.
"""


@dataclass(frozen=True, slots=True)
class OperatorHolder:
    """One operator, the scopes they hold, and the nearest deadline among them."""

    user_id: UserId
    scopes: frozenset[str]
    expires_at: datetime | None
    """The earliest ``expires_at`` among this holder's active grants, or ``None``
    when at least one of their grants never lapses."""

    @property
    def is_expiring(self) -> bool:
        return self.expires_at is not None


@dataclass(frozen=True, slots=True)
class OperatorGrantDetail:
    """One stored grant, un-folded: who holds it, who issued it, and why.

    ``OperatorHolder`` answers *who holds what until when* — the right shape for
    a summary, and the wrong shape for an audit. Folding per-scope grants into one
    row per holder necessarily discards the two fields that carry provenance: a
    holder with three grants has three different issuers and three different
    reasons, and a single ``rationale`` on the holder row would have to pick one
    arbitrarily or drop them all. NEX-076 recorded exactly that gap — the reason
    was written and never read back.

    This row keeps the grant's own shape so the reason reaches the reader:

    * ``granted_by`` is ``None`` for a configuration grant (the seed path), which
      is an honest "no human issued this", never a missing field.
    * ``rationale`` is the issuer's stated reason. A human-issued grant normally
      carries one; a configuration grant has none, because its reason is the
      deployment itself. It is typed ``str | None`` because a configuration grant
      legitimately has neither — and because a grant whose issuer's account was
      deleted keeps its rationale while losing ``granted_by`` (NEX-080).

    ``revoked_at``/``revoked_by`` are the revocation provenance NEX-079 wrote and
    nothing could read. Both are ``None`` on a live grant; a revoked grant carries
    the instant it ended and, unless the revoker's account has since been deleted,
    who ended it. They are reported on this same row rather than in a separate
    shape so a reader sees the grant's whole life in one place.
    """

    user_id: UserId
    scope: str
    granted_at: datetime
    granted_by: UserId | None
    expires_at: datetime | None
    rationale: str | None
    revoked_at: datetime | None = None
    revoked_by: UserId | None = None


@dataclass(frozen=True, slots=True)
class OperatorGrantSnapshot:
    """Every active operator at one instant, in stable order.

    ``source_name`` names the adapter that answered, so a reader cannot mistake a
    process-local demo store for a durable deployment-wide one. ``as_of`` is the
    instant the answer describes, taken from the gate's clock so it agrees with
    the access-log entry the same request wrote.

    ``holders`` is the summary fold (one row per operator); ``grants`` is the
    un-folded detail behind it (one row per stored grant). Both describe the same
    active set at the same instant — they are two views, not two queries — so a
    reader can reconcile them without trusting either alone. The detail exists
    because the fold cannot carry per-grant provenance: see
    :class:`OperatorGrantDetail`.
    """

    holders: tuple[OperatorHolder, ...]
    grants: tuple[OperatorGrantDetail, ...]
    as_of: datetime
    source_name: str

    @property
    def holder_count(self) -> int:
        return len(self.holders)

    @property
    def grant_count(self) -> int:
        return len(self.grants)


class ListOperatorGrants:
    """Return every currently-active operator grant to an authorized operator."""

    def __init__(
        self,
        *,
        grants: OperatorGrantRepository,
        operator_gate: OperatorGate,
        clock: Clock,
    ) -> None:
        self._grants = grants
        self._operator_gate = operator_gate
        self._clock = clock

    def run(self, principal: Principal) -> OperatorGrantSnapshot:
        # Gate first: a refused caller must not learn how many operators exist,
        # and the gate records this grant before we read the store.
        self._operator_gate.authorize(principal, operation=OPERATION)

        now = self._clock.now()
        active = self._grants.active_grants(now=now)
        return OperatorGrantSnapshot(
            holders=_summarise(active),
            grants=_detail(active),
            as_of=now,
            source_name=self._grants.name,
        )


@dataclass(frozen=True, slots=True)
class RevokedOperatorGrantSnapshot:
    """Every grant that ended by revocation, in the store's stable order (NEX-080).

    Read-only history, unlike :class:`OperatorGrantSnapshot`: it describes facts
    that have already happened, so it carries no ``as_of``. ``source_name`` still
    names the adapter, for the same reason — a reader must not mistake a
    process-local demo store for a durable deployment-wide one.

    There is no fold here. A revocation is one fact about one grant, and folding
    it per holder would have to discard the very provenance the endpoint exists to
    expose: which of a holder's several revoked grants was ended by whom, and when.
    """

    grants: tuple[OperatorGrantDetail, ...]
    source_name: str

    @property
    def grant_count(self) -> int:
        return len(self.grants)


class ListRevokedOperatorGrants:
    """Return every revoked operator grant to an authorized operator (NEX-080).

    NEX-079 recorded a revocation as a fact — ``revoked_at`` and ``revoked_by`` on
    the grant — precisely so an auditor could ask "who removed whose authority, and
    when". Nothing read it back. The write was durable and the read did not exist,
    which is the same half-built loop NEX-076 closed for ``rationale``.

    Gated by the same ``operator-access-report`` scope as the holder report: reading
    the revocation history of the platform is exactly the authority that report
    already needs, and minting a fifth token for it would force every auditor to be
    granted twice to answer one question.
    """

    def __init__(
        self,
        *,
        grants: OperatorGrantRepository,
        operator_gate: OperatorGate,
    ) -> None:
        self._grants = grants
        self._operator_gate = operator_gate

    def run(self, principal: Principal) -> RevokedOperatorGrantSnapshot:
        # Gate first, never after: a refused caller must not learn how much
        # authority has been removed, and the gate records the attempt.
        self._operator_gate.authorize(principal, operation=OPERATION)

        revoked = self._grants.revoked_grants()
        return RevokedOperatorGrantSnapshot(
            grants=_detail(revoked),
            source_name=self._grants.name,
        )


def _detail(grants: tuple[OperatorGrant, ...]) -> tuple[OperatorGrantDetail, ...]:
    """One detail row per grant, in the store's stable order.

    The fold in :func:`_summarise` answers "who holds what"; this answers "why,
    and who said so". Both read the same ``active`` tuple, so the summary and its
    detail cannot describe different instants or different sets. ``rationale`` is
    unwrapped to its text here — the value object is the domain's guard at the
    boundary, and a response should carry the operator's sentence, not a wrapper.
    """
    return tuple(
        OperatorGrantDetail(
            user_id=grant.user_id,
            scope=grant.scope,
            granted_at=grant.granted_at,
            granted_by=grant.granted_by,
            expires_at=grant.expires_at,
            rationale=grant.rationale.value if grant.rationale is not None else None,
            revoked_at=grant.revoked_at,
            revoked_by=grant.revoked_by,
        )
        for grant in grants
    )


def _summarise(grants: tuple[OperatorGrant, ...]) -> tuple[OperatorHolder, ...]:
    """Fold per-scope grants into one row per holder, preserving first-seen order.

    A holder with several scopes appears once. ``expires_at`` is the *earliest*
    deadline among their grants unless one is unbounded, in which case it is
    ``None`` — the strongest grant a holder has is the one that describes how long
    they keep access, so a summary that reported a nearby deadline for someone who
    also holds a permanent grant would understate the exposure.
    """
    scopes: dict[UserId, set[str]] = {}
    unbounded: set[UserId] = set()
    nearest: dict[UserId, datetime] = {}
    order: list[UserId] = []

    for grant in grants:
        if grant.user_id not in scopes:
            scopes[grant.user_id] = set()
            order.append(grant.user_id)
        scopes[grant.user_id].add(grant.scope)
        if grant.expires_at is None:
            unbounded.add(grant.user_id)
        else:
            current = nearest.get(grant.user_id)
            if current is None or grant.expires_at < current:
                nearest[grant.user_id] = grant.expires_at

    holders: list[OperatorHolder] = []
    for user_id in order:
        expires_at = None if user_id in unbounded else nearest.get(user_id)
        holders.append(
            OperatorHolder(
                user_id=user_id,
                scopes=frozenset(scopes[user_id]),
                expires_at=expires_at,
            )
        )
    return tuple(holders)


ISSUE_OPERATION = "operator-grant-issue"
"""The scope that gates *issuing* a grant.

Deliberately not the same token as ``OPERATION``. Reading who holds a grant and
handing one out are different authorities, and a deployment that wants only a
few people able to widen the allowlist can say so by granting this scope to
narrower set than ``operator-access-report``. Reusing the read scope here would
have made every auditor able to mint operators, which is the escalation this
whole subsystem exists to make visible.
"""

REVOKE_OPERATION = "operator-grant-revoke"
"""The scope that gates *revoking* a grant (NEX-079).

A third token, distinct from reading (``operator-access-report``) and issuing
(``operator-grant-issue``), because taking authority away is its own authority. A
deployment may reasonably want an incident responder who can *stop* an operator
without being able to *make* one, and folding revoke into issue would have forced
them to grant both to get the first. Keeping it separate also means the answer to
"who removed this grant" is a narrower, auditable set than "who could have".
"""

KNOWN_OPERATOR_OPERATIONS = frozenset(
    {
        ISSUE_OPERATION,
        REVOKE_OPERATION,
        OPERATION,
        "access-log-retention",
        "audit-integrity",
        "policy-approve",
        "policy-draft",
        "policy-history",
        "policy-publish",
        "policy-rollback",
        "withholding-report",
    }
)
"""Every scope this build knows how to check.

A grant naming anything else would be stored, listed, and then enforce nothing,
because no use case ever asks for that token. Refusing an unknown scope turns a
silent no-op into a loud request error for the operator who typed it. Adding a
new operational view means adding its token here in the same change — the set is
the single place that answers "what can an operator scope actually cover".
"""


@dataclass(frozen=True, slots=True)
class IssueOperatorGrantRequest:
    """A bounded grant one operator asks to hand to another user.

    ``expires_at`` is required and there is no ``granted_by`` field: the issuer is
    the authenticated principal, stamped by the application layer. A request body
    that could name its own issuer would let a caller attribute a widening to
    someone else.

    ``rationale`` is required: the operator's own words for *why* this user's
    authority is being widened. The grant row now has a column for it, so a
    required field is a record that is written, not one validated and discarded.
    Before this, intent rested on ``granted_by`` plus the gate's access log, which
    records the issuer and the instant but never the reason — enough to know *who*
    widened *what*, never *why*. See ``OperatorGrantRationale`` for the bounds.
    """

    user_id: UserId
    scope: str
    expires_at: datetime
    rationale: OperatorGrantRationale


class IssueOperatorGrant:
    """Issue a time-bounded operator grant to another user (NEX-074).

    Until this slice ``OperatorGrant`` carried ``expires_at`` and ``granted_by``
    and the store was durable, but nothing could ever *write* a human-issued
    grant: the only writer was the configuration seed path, which issues unbounded
    grants with ``granted_by=None``. So the bounded, attributable grant — the
    whole point of the record — was unreachable, and the documented remedy for a
    lapsed incident grant ("issue it through the store") had no caller.

    Four refusals make this safe, and each is a real escalation if omitted:

    * **Bounded only.** An unbounded grant comes from deployment configuration,
      where it is reviewed with the deployment. An operator may extend access for
      a window, never permanently.
    * **No self-grant.** An operator cannot widen their own authority, which is
      the human form of the rule that an agent may never grant itself permission.
    * **Attenuation.** Every scope issued must already be held by the issuer. A
      grant can narrow authority across people, never amplify it: an operator with
      ``withholding-report`` cannot mint a ``policy-publish`` holder.
    * **Known scope.** See :data:`KNOWN_OPERATOR_OPERATIONS`.
    """

    def __init__(
        self,
        *,
        grants: OperatorGrantRepository,
        operator_gate: OperatorGate,
        clock: Clock,
        user_exists: Callable[[UserId], bool],
    ) -> None:
        self._grants = grants
        self._operator_gate = operator_gate
        self._clock = clock
        self._user_exists = user_exists

    def execute(self, principal: Principal, request: IssueOperatorGrantRequest) -> OperatorGrant:
        # Gate first, exactly like every other operator use case: a refused caller
        # reaches no store and leaves no trace.
        self._operator_gate.authorize(principal, operation=ISSUE_OPERATION)

        if request.user_id == principal.user_id:
            raise Conflict(
                "an operator cannot issue a grant to themselves; a second operator must issue it"
            )
        if not self._user_exists(request.user_id):
            # A grant to a user id that does not exist would confer nothing and
            # would sit in the holder report looking like a live operator.
            raise ValidationError(f"no such user {request.user_id}")
        if request.scope not in KNOWN_OPERATOR_OPERATIONS:
            raise ValidationError(
                f"unknown operator scope {request.scope!r}; "
                f"expected one of {sorted(KNOWN_OPERATOR_OPERATIONS)}"
            )
        self._require_attenuation(principal, request.scope)

        now = self._clock.now()
        # Awareness is checked before the comparison for the same reason the domain
        # checks it before its own: comparing a naive deadline to the aware clock
        # raises TypeError, so a caller that bypassed the request schema would get a
        # 500 instead of the fail-closed refusal a bad deadline deserves (NEX-078).
        if request.expires_at.tzinfo is None or request.expires_at.utcoffset() is None:
            raise ValidationError(
                "an operator grant must expire at a timezone-aware instant; "
                "a naive datetime does not identify when access ends"
            )
        if request.expires_at <= now:
            raise ValidationError("an operator grant must expire in the future")
        grant = OperatorGrant(
            user_id=request.user_id,
            scope=request.scope,
            granted_at=now,
            granted_by=principal.user_id,
            expires_at=request.expires_at,
            rationale=request.rationale,
        )
        self._grants.add(grant)
        return grant

    def _require_attenuation(self, principal: Principal, scope: str) -> None:
        """Refuse a scope the issuer does not hold.

        Checked against the gate's live scopes rather than against configuration,
        so an issuer whose own grant has lapsed cannot use a stale memory of it to
        hand the same scope onward. A wildcard holder may issue anything, because
        the wildcard already covers it.
        """
        held = self._operator_gate.scopes_for(principal.user_id)
        if WILDCARD in held or scope in held:
            return
        raise Conflict(
            f"an operator may only issue a scope they hold; "
            f"{principal.user_id} does not hold {scope!r}"
        )


@dataclass(frozen=True, slots=True)
class RevokeOperatorGrantRequest:
    """Ask to take one scope away from one user (NEX-079).

    Scoped to ``(user, scope)`` rather than to a grant id on purpose. An operator
    revoking authority means "this person must no longer hold this permission", not
    "delete this particular row" — and a caller forced to name a row would have to
    list first, then race a re-issue between the list and the revoke. Naming the
    permission is both the intent and the safe unit.

    There is no ``revoked_by`` field: the revoker is the authenticated principal,
    stamped by the use case, for the same reason :class:`IssueOperatorGrantRequest`
    takes no ``granted_by``.
    """

    user_id: UserId
    scope: str


class RevokeOperatorGrant:
    """Revoke a user's live grant of one scope (NEX-079).

    Before this, a grant could only end by expiry. That made a mistaken hand-out
    live for its whole term, and a *configuration* grant — which never expires —
    impossible to end without editing the deployment seed and redeploying. This
    use case closes both: a revocation is effective immediately and is recorded as
    an attributable fact on the grant rather than applied by deleting it.

    Three refusals, each a real escalation if omitted:

    * **Unknown scope.** Revoking a token no use case checks would report success
      while changing nothing. Refused, so a typo is a loud error, mirroring issue.
    * **No self-revoke.** An operator cannot revoke their own grant. Removing your
      own authority is not a security improvement the platform needs an operator
      for, and allowing it is the mirror image of self-grant: a single compromised
      operator session could strip a scope from every peer it names, so requiring a
      *second* operator to revoke is the same separation of duties that stops
      self-granting. (Self-service offboarding is a distinct feature with its own
      threat model; it is not this one.)
    * **Nothing to revoke is a conflict, not a silent success.** If the holder has
      no live grant of that scope, the request is refused with 409. Reporting
      success for a no-op would let an incident responder believe authority was
      removed when it had already lapsed — or, worse, when they named the wrong
      user. The operator learns the truth.
    """

    def __init__(
        self,
        *,
        grants: OperatorGrantRepository,
        operator_gate: OperatorGate,
        clock: Clock,
    ) -> None:
        self._grants = grants
        self._operator_gate = operator_gate
        self._clock = clock

    def execute(self, principal: Principal, request: RevokeOperatorGrantRequest) -> int:
        # Gate first, exactly like issue: a refused caller reaches no store.
        self._operator_gate.authorize(principal, operation=REVOKE_OPERATION)

        if request.scope not in KNOWN_OPERATOR_OPERATIONS:
            raise ValidationError(
                f"unknown operator scope {request.scope!r}; "
                f"expected one of {sorted(KNOWN_OPERATOR_OPERATIONS)}"
            )
        if request.user_id == principal.user_id:
            raise Conflict(
                "an operator cannot revoke their own grant; a second operator must revoke it"
            )

        revoked = self._grants.revoke(
            request.user_id, request.scope, at=self._clock.now(), by=principal.user_id
        )
        if revoked == 0:
            # No live grant to remove. Refused rather than reported as done: a
            # responder must never be told authority was taken away when it was not.
            raise Conflict(f"{request.user_id} holds no live {request.scope!r} grant to revoke")
        return revoked


__all__ = [
    "ISSUE_OPERATION",
    "KNOWN_OPERATOR_OPERATIONS",
    "OPERATION",
    "REVOKE_OPERATION",
    "IssueOperatorGrant",
    "IssueOperatorGrantRequest",
    "ListOperatorGrants",
    "ListRevokedOperatorGrants",
    "OperatorGrantDetail",
    "OperatorGrantSnapshot",
    "OperatorHolder",
    "RevokeOperatorGrant",
    "RevokeOperatorGrantRequest",
    "RevokedOperatorGrantSnapshot",
]
