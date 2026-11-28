"""How long the operator access log is kept, and how much of it may be dropped (NEX-075).

NEX-072 made the record of a privileged read durable, and it said what it would
not do: nothing prunes the table. That was the honest trade at the time —
durability first, retention as a follow-up — but an unbounded audit table is its
own failure. It grows without limit, every ``recent`` read slows as it fills, and
the one thing a retention window must never be is *implicit*. A ``DELETE``
someone adds later under deadline pressure, with no stated window and no stated
authority, is how an audit trail quietly becomes a partial one.

So the window is a domain value, not a constant at a call site, and it carries
its own floor. Three rules, and each is a real failure if dropped:

* **A floor, so retention cannot be shortened into meaninglessness.** A window of
  a day would let the next privileged read erase the evidence of the last one,
  which is precisely the property an audit trail exists to deny. The floor is
  enforced on construction, so no configuration and no request can go below it.
* **A ceiling, so a typo cannot pin every row forever.** ``36500`` days is a
  plausible typo for ``3650``; without a ceiling a deployment could believe it
  has retention when it has none. The ceiling makes "keep forever" an explicit
  deployment decision (leave retention disabled), never an accidental one.
* **A cutoff, computed from one clock.** The window is turned into an instant
  once, by the caller's clock, and everything at or after that instant is
  outside the purge's reach. The adapter refuses a row it was not given a
  mandate for; the boundary is here so the arithmetic is in one place and
  testable without a database.

This module is pure: no clock, no store, no framework. It answers *what* may be
deleted; the application use case supplies *when* and *who*, and the adapter does
the deleting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from notes_service.domain.errors import ValidationError

MINIMUM_RETENTION = timedelta(days=90)
"""The shortest window a deployment may configure.

A quarter is the shortest span that covers a realistic incident-to-review cycle:
a privileged read is noticed, investigated, and reviewed over weeks, not days.
No external regulation is cited here because this repository names none — this is
a defensible internal floor, and raising it for a specific regime is a compliance
decision that belongs in a deployment, never a lowering of this one.
"""

MAXIMUM_RETENTION = timedelta(days=3650)
"""Ten years: the longest window this build accepts.

Beyond this the difference between "ten years" and "forever" is not meaningful
for a table that grows one row per privileged read, so a deployment that wants
to keep everything should leave retention disabled rather than configure a
number it will never reach. The ceiling exists to catch an extra zero, not to
express a preference.
"""


@dataclass(frozen=True, slots=True)
class OperatorAccessRetentionPolicy:
    """The retention window for the operator access log.

    Constructed from configuration at startup so a bad window fails the boot, not
    the first purge an operator runs. The window is inclusive at its near edge:
    an entry stamped exactly ``window`` ago is *kept*, because a record is only
    deleted when it is strictly older than the cutoff.
    """

    window: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.window, timedelta):
            raise ValidationError("retention window must be a timedelta")
        if self.window < MINIMUM_RETENTION:
            raise ValidationError(
                f"retention window {self.window} is shorter than the floor "
                f"{MINIMUM_RETENTION}; a shorter window would let one privileged "
                "read erase the record of the previous one"
            )
        if self.window > MAXIMUM_RETENTION:
            raise ValidationError(
                f"retention window {self.window} exceeds the ceiling "
                f"{MAXIMUM_RETENTION}; to keep the log indefinitely, disable "
                "retention instead of configuring a very long window"
            )

    def cutoff(self, *, now: datetime) -> datetime:
        """The instant before which entries may be deleted.

        Everything strictly older than this is outside the window. The boundary
        is computed from the caller's clock, the same clock that stamps every
        access entry, so the window a purge honours is the window the log was
        written under.
        """
        return now - self.window

    def covers(self, *, at: datetime, now: datetime) -> bool:
        """Whether an entry stamped ``at`` is still inside the window at ``now``.

        An entry exactly at the cutoff is covered: it is kept. Stated as a method
        so the boundary is asserted in one place rather than re-derived by each
        caller and each test.
        """
        return at >= self.cutoff(now=now)


__all__ = [
    "MAXIMUM_RETENTION",
    "MINIMUM_RETENTION",
    "OperatorAccessRetentionPolicy",
]
