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
from notes_service.domain.identity.operator_grant import OperatorGrant
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
class OperatorGrantSnapshot:
    """Every active operator at one instant, in stable order.

    ``source_name`` names the adapter that answered, so a reader cannot mistake a
    process-local demo store for a durable deployment-wide one. ``as_of`` is the
    instant the answer describes, taken from the gate's clock so it agrees with
    the access-log entry the same request wrote.
    """

    holders: tuple[OperatorHolder, ...]
    as_of: datetime
    source_name: str

    @property
    def holder_count(self) -> int:
        return len(self.holders)


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
            as_of=now,
            source_name=self._grants.name,
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

KNOWN_OPERATOR_OPERATIONS = frozenset(
    {
        ISSUE_OPERATION,
        OPERATION,
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

    There is no ``rationale`` field, deliberately: the grant row has no column for
    one, so a required free-text field would be validated and then discarded — worse
    than absent, because it reads as a record that is never written. Intent is
    carried today by ``granted_by`` plus the gate's access log, which records the
    issuer, the ``operator-grant-issue`` operation, and the instant.
    """

    user_id: UserId
    scope: str
    expires_at: datetime


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
        if request.expires_at <= now:
            raise ValidationError("an operator grant must expire in the future")
        grant = OperatorGrant(
            user_id=request.user_id,
            scope=request.scope,
            granted_at=now,
            granted_by=principal.user_id,
            expires_at=request.expires_at,
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


__all__ = [
    "ISSUE_OPERATION",
    "KNOWN_OPERATOR_OPERATIONS",
    "OPERATION",
    "IssueOperatorGrant",
    "IssueOperatorGrantRequest",
    "ListOperatorGrants",
    "OperatorGrantSnapshot",
    "OperatorHolder",
]
