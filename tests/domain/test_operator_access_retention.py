"""How long the operator access log is kept, and how little may be dropped (NEX-075).

The purge itself is covered elsewhere; this file covers the *window*, because the
window is where retention either means something or quietly becomes a way to
erase evidence. Three properties, each a real failure if dropped:

* **a floor.** A window shorter than a quarter would let one privileged read erase
  the record of the previous one, which is the exact property an audit trail
  exists to deny. Enforced on construction, so no configuration reaches it.
* **a ceiling.** ``36500`` is a plausible typo for ``3650``, and a deployment
  believing it has retention when it does not is worse than none. "Keep forever"
  is expressed by disabling retention, never by a very long number.
* **one boundary, inclusive at its near edge.** An entry stamped exactly ``window``
  ago is *kept*, and the same rule decides both what the adapter deletes and what
  ``covers`` reports — so the two cannot disagree at the oldest surviving row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from notes_service.domain.errors import ValidationError
from notes_service.domain.governance.retention import (
    MAXIMUM_RETENTION,
    MINIMUM_RETENTION,
    OperatorAccessRetentionPolicy,
)

NOW = datetime(2026, 11, 27, 17, 0, tzinfo=UTC)


def test_the_floor_is_a_quarter() -> None:
    """Stated as a literal so lowering it is a visible edit, not a config change."""
    assert timedelta(days=90) == MINIMUM_RETENTION


def test_a_window_at_the_floor_is_accepted() -> None:
    """The floor is a floor, not an exclusive bound: 90 days is allowed."""
    policy = OperatorAccessRetentionPolicy(window=MINIMUM_RETENTION)
    assert policy.window == MINIMUM_RETENTION


def test_a_window_below_the_floor_is_refused() -> None:
    """A one-day window would let today's read erase yesterday's evidence."""
    with pytest.raises(ValidationError):
        OperatorAccessRetentionPolicy(window=timedelta(days=1))


def test_a_window_one_second_below_the_floor_is_refused() -> None:
    """The boundary is exact, so an off-by-one is caught rather than tolerated."""
    with pytest.raises(ValidationError):
        OperatorAccessRetentionPolicy(window=MINIMUM_RETENTION - timedelta(seconds=1))


def test_a_window_at_the_ceiling_is_accepted() -> None:
    policy = OperatorAccessRetentionPolicy(window=MAXIMUM_RETENTION)
    assert policy.window == MAXIMUM_RETENTION


def test_a_window_above_the_ceiling_is_refused() -> None:
    """``36500`` (an extra zero on ``3650``) must fail, not pin every row forever."""
    with pytest.raises(ValidationError):
        OperatorAccessRetentionPolicy(window=timedelta(days=36500))


def test_a_non_timedelta_window_is_refused() -> None:
    """A bare number would be interpreted differently by every caller."""
    with pytest.raises(ValidationError):
        OperatorAccessRetentionPolicy(window=365)  # type: ignore[arg-type]


def test_the_cutoff_is_the_window_before_now() -> None:
    policy = OperatorAccessRetentionPolicy(window=timedelta(days=90))
    assert policy.cutoff(now=NOW) == NOW - timedelta(days=90)


def test_an_entry_exactly_at_the_cutoff_is_covered() -> None:
    """The near edge is inclusive: a record is only deleted when strictly older."""
    policy = OperatorAccessRetentionPolicy(window=timedelta(days=90))
    at_cutoff = policy.cutoff(now=NOW)
    assert policy.covers(at=at_cutoff, now=NOW) is True


def test_an_entry_one_second_before_the_cutoff_is_not_covered() -> None:
    """The complement of the inclusive edge: strictly older is outside the window."""
    policy = OperatorAccessRetentionPolicy(window=timedelta(days=90))
    just_outside = policy.cutoff(now=NOW) - timedelta(seconds=1)
    assert policy.covers(at=just_outside, now=NOW) is False


def test_a_recent_entry_is_covered() -> None:
    policy = OperatorAccessRetentionPolicy(window=timedelta(days=90))
    assert policy.covers(at=NOW - timedelta(days=1), now=NOW) is True
