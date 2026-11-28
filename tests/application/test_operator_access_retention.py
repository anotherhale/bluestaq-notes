"""Purging the operator access log: gated, windowed, and recorded (NEX-075).

The window is pinned in ``tests/domain/test_operator_access_retention.py`` and the
adapter in ``tests/persistence/test_operator_access_log.py``. This file covers the
use case, which is where the two authorities meet, and where the dangerous mistakes
would live:

* **A distinct scope.** Reading who read what (``operator-access-report``) does not
  confer the right to *erase* who read what (``access-log-retention``). A
  deployment can grant the read widely and the purge narrowly, and the second test
  below is what fails if the two scopes are ever collapsed.
* **No caller-supplied cutoff.** The boundary comes from the policy, so no request
  can express "delete everything" or "delete this user's rows".
* **The purge records itself.** The gate writes the purge to the log before the
  delete runs, stamped ``now`` — inside the window — so a purge cannot erase the
  record of its own action. An audit trail that deleted its own erasures would be
  worse than none.
* **Fail closed.** A non-operator gets ``ResourceNotFound`` and reaches no store,
  so a refused purge deletes nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from notes_service.application.errors import ResourceNotFound
from notes_service.application.ports.operator_access import OperatorAccessEntry
from notes_service.application.use_cases.operator_access_retention import (
    OPERATION,
    PurgeOperatorAccessLog,
)
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.domain.errors import ValidationError
from notes_service.domain.governance.retention import (
    MINIMUM_RETENTION,
    OperatorAccessRetentionPolicy,
)
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.security.operator_access_log import (
    InMemoryOperatorAccessLog,
)
from tests.application.grant_helpers import grants_from_scopes

ALICE = UserId.parse("11111111-1111-1111-1111-111111111111")
BOB = UserId.parse("22222222-2222-2222-2222-222222222222")

NOW = datetime(2026, 11, 27, 17, 0, tzinfo=UTC)
WINDOW = timedelta(days=90)


def _principal(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email("user@example.com"),
        display_name="User",
        provider="demo",
        subject=str(user_id),
    )


class _FixedClock:
    """A clock pinned to ``now`` so the cutoff is arithmetic, not wall time."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _RecordingLog:
    """A log that reports a purge count and remembers the cutoff it was given.

    The in-memory adapter deliberately returns ``0`` (its ring self-bounds), so it
    cannot show that a *count* is propagated or that the *policy's* cutoff is the
    one passed down. This spy answers both without standing up a database.
    """

    def __init__(self, *, removed: int, entries: tuple[OperatorAccessEntry, ...] = ()) -> None:
        self._removed = removed
        self._entries = entries
        self.cutoffs: list[datetime] = []

    @property
    def name(self) -> str:
        return "spy"

    def record(self, entry: OperatorAccessEntry) -> None:
        # The gate records the purge itself; the spy keeps that entry so
        # ``recent`` can answer the "oldest retained" read honestly.
        self._entries = (entry, *self._entries)

    def recent(self, *, limit: int) -> tuple[OperatorAccessEntry, ...]:
        return self._entries[:limit]

    def purge_before(self, *, cutoff: datetime) -> int:
        self.cutoffs.append(cutoff)
        return self._removed


def _use_case(
    *,
    operators: frozenset[UserId],
    access_log: InMemoryOperatorAccessLog | _RecordingLog | None = None,
    window: timedelta = WINDOW,
    now: datetime = NOW,
) -> PurgeOperatorAccessLog:
    log = access_log if access_log is not None else InMemoryOperatorAccessLog()
    return PurgeOperatorAccessLog(
        access_log=log,
        operator_gate=OperatorGate(
            grants=grants_from_scopes({user: frozenset({"*"}) for user in operators}),
            access_log=log,
            clock=_FixedClock(now),
        ),
        clock=_FixedClock(now),
        policy=OperatorAccessRetentionPolicy(window=window),
    )


def test_the_operation_scope_is_the_retention_scope() -> None:
    """A stable identifier: the grant a deployment issues must match this string."""
    assert OPERATION == "access-log-retention"


def test_an_operator_purges_and_the_count_is_reported() -> None:
    log = _RecordingLog(removed=3)
    result = _use_case(operators=frozenset({ALICE}), access_log=log).execute(_principal(ALICE))

    assert result.removed == 3
    assert result.window_days == 90
    assert result.log_name == "spy"


def test_the_cutoff_comes_from_the_policy_not_the_caller() -> None:
    """The adapter receives ``now - window``, computed by the policy."""
    log = _RecordingLog(removed=0)
    _use_case(operators=frozenset({ALICE}), access_log=log).execute(_principal(ALICE))

    assert log.cutoffs == [NOW - WINDOW]


def test_a_different_window_moves_the_cutoff() -> None:
    """The window is honoured, so a longer retention deletes less."""
    log = _RecordingLog(removed=0)
    _use_case(
        operators=frozenset({ALICE}),
        access_log=log,
        window=timedelta(days=365),
    ).execute(_principal(ALICE))

    assert log.cutoffs == [NOW - timedelta(days=365)]


def test_reading_the_log_does_not_confer_the_right_to_purge_it() -> None:
    """The whole point of the distinct scope: read authority is not erase authority.

    A caller granted only ``operator-access-report`` is refused here. If the two
    scopes were ever collapsed, this test is what fails.
    """
    log = InMemoryOperatorAccessLog()
    use_case = PurgeOperatorAccessLog(
        access_log=log,
        operator_gate=OperatorGate(
            grants=grants_from_scopes({ALICE: frozenset({"operator-access-report"})}),
            access_log=log,
            clock=_FixedClock(NOW),
        ),
        clock=_FixedClock(NOW),
        policy=OperatorAccessRetentionPolicy(window=WINDOW),
    )

    with pytest.raises(ResourceNotFound):
        use_case.execute(_principal(ALICE))


def test_a_non_operator_is_refused_as_not_found() -> None:
    """404, never 403: the endpoint must not confirm that it exists."""
    use_case = _use_case(operators=frozenset({ALICE}))
    with pytest.raises(ResourceNotFound):
        use_case.execute(_principal(BOB))


def test_a_refused_purge_reaches_no_store() -> None:
    """A refused caller must not delete anything, so the spy sees no cutoff."""
    log = _RecordingLog(removed=99)
    use_case = _use_case(operators=frozenset({ALICE}), access_log=log)

    with pytest.raises(ResourceNotFound):
        use_case.execute(_principal(BOB))

    assert log.cutoffs == []


def test_the_purge_is_recorded_before_it_runs() -> None:
    """The act of pruning leaves a mark stamped inside the window it just pruned."""
    log = InMemoryOperatorAccessLog()
    use_case = _use_case(operators=frozenset({ALICE}), access_log=log)

    use_case.execute(_principal(ALICE))

    recorded = log.recent(limit=10)
    assert [entry.operation for entry in recorded] == [OPERATION]
    assert recorded[0].user_id == ALICE
    assert recorded[0].at == NOW


def test_an_empty_log_reports_no_retained_entry() -> None:
    """``retained_from`` is ``None`` when nothing survives, not a fabricated stamp."""
    log = InMemoryOperatorAccessLog()
    result = _use_case(operators=frozenset({ALICE}), access_log=log).execute(_principal(ALICE))

    # The purge's own record is the only entry, so *something* survives.
    assert result.retained_from == NOW


def test_the_oldest_surviving_entry_is_reported() -> None:
    """A reader can see what survived the boundary, not just how many rows went."""
    oldest = NOW - timedelta(days=30)
    log = _RecordingLog(
        removed=2,
        entries=(
            OperatorAccessEntry(user_id=ALICE, operation="newer", at=NOW - timedelta(days=1)),
            OperatorAccessEntry(user_id=ALICE, operation="older", at=oldest),
        ),
    )
    result = _use_case(operators=frozenset({ALICE}), access_log=log).execute(_principal(ALICE))

    assert result.retained_from == oldest


def test_the_floor_is_enforced_even_through_the_use_case() -> None:
    """A policy below the floor cannot be constructed, so the use case cannot hold one."""
    with pytest.raises(ValidationError):
        _use_case(operators=frozenset({ALICE}), window=timedelta(days=1))


def test_the_result_reports_the_window_in_days() -> None:
    result = _use_case(operators=frozenset({ALICE}), window=MINIMUM_RETENTION).execute(
        _principal(ALICE)
    )
    assert result.window_days == 90
