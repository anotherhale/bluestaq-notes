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

It takes no unit of work: reading grants mutates nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from notes_service.application.ports.clock import Clock
from notes_service.application.ports.operator_grants import OperatorGrantRepository
from notes_service.application.use_cases.operator_gate import OperatorGate
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


__all__ = [
    "OPERATION",
    "ListOperatorGrants",
    "OperatorGrantSnapshot",
    "OperatorHolder",
]
