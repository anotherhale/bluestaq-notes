"""The operator access record can be read back, and the read is itself recorded (R36).

R36 gave the operator allowlist a record: every granted platform-wide read
appends an entry. Nothing read that record, so "who read the totals" was still
unanswerable from inside the system — a log with no reader is not an audit
trail. ``GetOperatorAccessReport`` is the reader.

These tests pin the properties that make it an audit trail rather than a list:

* an operator sees the grants that happened, newest first, naming the actor;
* a non-operator is refused as ``ResourceNotFound`` so the endpoint stays
  indistinguishable from a path that does not exist, and an empty allowlist
  admits nobody;
* reading the log **appends to it** — inspecting the trail is itself a granted
  operator read, so it cannot be done silently;
* a refused caller adds nothing, so the log answers "who read this" rather than
  "who was turned away";
* the caller-supplied ``limit`` is bounded, and a bounded window says so via
  ``truncated`` instead of implying it is everything.

The adapter that stores the entries is covered in
``tests/bootstrap/test_operator_access_log.py``; the gate in
``tests/application/test_operator_gate.py``. This file covers the read side.
"""

from __future__ import annotations

import pytest

from notes_service.application.errors import ResourceNotFound
from notes_service.application.ports.operator_access import OperatorAccessEntry
from notes_service.application.use_cases.operator_access_report import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    GetOperatorAccessReport,
)
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.security.clock import SystemClock
from notes_service.infrastructure.security.operator_access_log import (
    InMemoryOperatorAccessLog,
)

ALICE = UserId.parse("11111111-1111-1111-1111-111111111111")
BOB = UserId.parse("22222222-2222-2222-2222-222222222222")


def _principal(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email("user@example.com"),
        display_name="User",
        provider="demo",
        subject=str(user_id),
    )


def _use_case(
    *,
    operators: frozenset[UserId],
    access_log: InMemoryOperatorAccessLog | None = None,
) -> GetOperatorAccessReport:
    return GetOperatorAccessReport(
        access_log=access_log or InMemoryOperatorAccessLog(),
        operator_gate=OperatorGate(
            operator_ids=operators,
            access_log=access_log or InMemoryOperatorAccessLog(),
            clock=SystemClock(),
        ),
    )


def test_an_operator_reads_the_record_naming_the_actor() -> None:
    """The record answers *who* read the totals, which was the whole point."""
    log = InMemoryOperatorAccessLog()
    use_case = _use_case(operators=frozenset({ALICE}), access_log=log)

    # Simulate a prior granted read of another operational view.
    log.record(
        OperatorAccessEntry(user_id=ALICE, operation="withholding-report", at=SystemClock().now())
    )

    snapshot = use_case.run(_principal(ALICE))

    # The prior read plus the read of the report itself are both present.
    operations = [entry.operation for entry in snapshot.entries]
    assert "withholding-report" in operations
    assert "operator-access-report" in operations
    assert all(entry.user_id == ALICE for entry in snapshot.entries)


def test_reading_the_log_appends_to_it() -> None:
    """Inspecting the trail must itself leave a trace, or it can be done silently."""
    log = InMemoryOperatorAccessLog()
    use_case = _use_case(operators=frozenset({ALICE}), access_log=log)

    assert log.recent(limit=10) == ()

    use_case.run(_principal(ALICE))

    recorded = log.recent(limit=10)
    assert len(recorded) == 1
    assert recorded[0].operation == "operator-access-report"
    assert recorded[0].user_id == ALICE


def test_entries_are_newest_first() -> None:
    """A reader wants the most recent use of the grant at the top."""
    log = InMemoryOperatorAccessLog()
    use_case = _use_case(operators=frozenset({ALICE}), access_log=log)

    use_case.run(_principal(ALICE))
    use_case.run(_principal(ALICE))

    operations = [entry.operation for entry in log.recent(limit=10)]
    assert operations == ["operator-access-report", "operator-access-report"]
    times = [entry.at for entry in log.recent(limit=10)]
    assert times == sorted(times, reverse=True)


def test_a_non_operator_is_refused_as_not_found() -> None:
    """404, never 403: the endpoint must not confirm that it exists."""
    use_case = _use_case(operators=frozenset({ALICE}))
    with pytest.raises(ResourceNotFound):
        use_case.run(_principal(BOB))


def test_a_refused_caller_leaves_no_trace() -> None:
    """A denial is not recorded, so the log answers "who read" not "who was refused"."""
    log = InMemoryOperatorAccessLog()
    use_case = _use_case(operators=frozenset({ALICE}), access_log=log)

    with pytest.raises(ResourceNotFound):
        use_case.run(_principal(BOB))

    assert log.recent(limit=10) == ()


def test_an_empty_allowlist_admits_nobody() -> None:
    """The default deployment grants this to no one, including a real user."""
    use_case = _use_case(operators=frozenset())
    with pytest.raises(ResourceNotFound):
        use_case.run(_principal(ALICE))


def test_the_limit_is_bounded_by_the_ceiling() -> None:
    """A caller cannot request an unbounded window."""
    log = InMemoryOperatorAccessLog(capacity=1000)
    for _ in range(MAX_LIMIT + 50):
        log.record(
            OperatorAccessEntry(
                user_id=ALICE, operation="withholding-report", at=SystemClock().now()
            )
        )
    use_case = _use_case(operators=frozenset({ALICE}), access_log=log)

    snapshot = use_case.run(_principal(ALICE), limit=10_000)

    assert snapshot.limit == MAX_LIMIT
    assert len(snapshot.entries) <= MAX_LIMIT


def test_a_non_positive_limit_is_treated_as_one() -> None:
    """A caller mistake is not a request for everything."""
    log = InMemoryOperatorAccessLog()
    use_case = _use_case(operators=frozenset({ALICE}), access_log=log)

    snapshot = use_case.run(_principal(ALICE), limit=0)

    assert snapshot.limit == 1


def test_the_snapshot_describes_the_adapter_that_answered() -> None:
    """A bounded, process-local window must not read as a durable history."""
    log = InMemoryOperatorAccessLog(capacity=7)
    use_case = _use_case(operators=frozenset({ALICE}), access_log=log)

    snapshot = use_case.run(_principal(ALICE))

    assert snapshot.capacity == 7
    assert snapshot.log_name == log.name
    assert snapshot.log_name  # non-empty: the reader can identify the adapter


def test_a_full_window_reports_itself_truncated() -> None:
    """``truncated`` must be true when the window may have hidden older entries."""
    log = InMemoryOperatorAccessLog()
    use_case = _use_case(operators=frozenset({ALICE}), access_log=log)

    use_case.run(_principal(ALICE), limit=1)

    snapshot = use_case.run(_principal(ALICE), limit=1)
    assert len(snapshot.entries) == snapshot.limit
    assert snapshot.truncated is True


def test_a_window_with_room_is_not_truncated() -> None:
    log = InMemoryOperatorAccessLog()
    use_case = _use_case(operators=frozenset({ALICE}), access_log=log)

    snapshot = use_case.run(_principal(ALICE), limit=DEFAULT_LIMIT)

    assert len(snapshot.entries) < snapshot.limit
    assert snapshot.truncated is False
