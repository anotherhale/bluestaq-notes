"""One operator gate, and a record of every granted read (R36).

Before this slice, ``GetWithholdingReport`` and ``SweepAuditIntegrity`` each
carried their own copy of the allowlist check. Two copies of a security gate can
drift, and neither recorded that it had been used — so "who read the platform
totals" could not be answered from inside the system at all.

The properties pinned here are the ones that make the gate safe to share:

* an empty allowlist admits nobody (the default deployment exposes nothing);
* a non-operator is refused as ``ResourceNotFound``, so the endpoint cannot be
  distinguished from a missing path;
* a granted read appends exactly one entry naming caller and operation;
* a refused read appends nothing, so an entry is evidence of authorisation;
* the entry is stamped by the gate's own clock, not by wall time, so the record
  agrees with the request that produced it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from notes_service.application.errors import ResourceNotFound
from notes_service.application.ports.clock import Clock
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.security.operator_access_log import (
    InMemoryOperatorAccessLog,
)

ALICE = UserId.parse("11111111-1111-1111-1111-111111111111")
BOB = UserId.parse("22222222-2222-2222-2222-222222222222")


class FixedClock(Clock):
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _principal(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email("user@example.com"),
        display_name="User",
        provider="demo",
        subject=str(user_id),
    )


def _gate(
    *,
    operators: frozenset[UserId],
    access_log: InMemoryOperatorAccessLog | None = None,
    now: datetime | None = None,
) -> OperatorGate:
    return OperatorGate(
        operator_ids=operators,
        access_log=access_log or InMemoryOperatorAccessLog(),
        clock=FixedClock(now or datetime(2026, 11, 27, 10, 0, tzinfo=UTC)),
    )


def test_an_operator_is_allowed() -> None:
    _gate(operators=frozenset({ALICE})).authorize(_principal(ALICE), operation="withholding-report")


def test_an_empty_allowlist_admits_nobody() -> None:
    with pytest.raises(ResourceNotFound):
        _gate(operators=frozenset()).authorize(_principal(ALICE), operation="withholding-report")


def test_a_non_operator_is_refused_as_not_found() -> None:
    with pytest.raises(ResourceNotFound):
        _gate(operators=frozenset({ALICE})).authorize(_principal(BOB), operation="audit-integrity")


def test_a_grant_records_the_caller_operation_and_clock_time() -> None:
    moment = datetime(2026, 11, 27, 10, 30, tzinfo=UTC)
    access_log = InMemoryOperatorAccessLog()
    _gate(operators=frozenset({ALICE}), access_log=access_log, now=moment).authorize(
        _principal(ALICE), operation="audit-integrity"
    )

    (entry,) = access_log.recent(limit=10)
    assert entry.user_id == ALICE
    assert entry.operation == "audit-integrity"
    assert entry.at == moment


def test_a_refusal_records_nothing() -> None:
    access_log = InMemoryOperatorAccessLog()
    gate = _gate(operators=frozenset({ALICE}), access_log=access_log)
    with pytest.raises(ResourceNotFound):
        gate.authorize(_principal(BOB), operation="audit-integrity")

    assert access_log.recent(limit=10) == ()


def test_repeated_grants_each_leave_their_own_entry() -> None:
    access_log = InMemoryOperatorAccessLog()
    gate = _gate(operators=frozenset({ALICE}), access_log=access_log)
    gate.authorize(_principal(ALICE), operation="withholding-report")
    gate.authorize(_principal(ALICE), operation="audit-integrity")

    entries = access_log.recent(limit=10)
    assert [entry.operation for entry in entries] == [
        "audit-integrity",
        "withholding-report",
    ]


def test_recent_is_newest_first_and_capped() -> None:
    access_log = InMemoryOperatorAccessLog(capacity=2)
    gate = _gate(operators=frozenset({ALICE}), access_log=access_log)
    base = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)

    for offset, operation in enumerate(("a", "b", "c")):
        OperatorGate(
            operator_ids=frozenset({ALICE}),
            access_log=access_log,
            clock=FixedClock(base + timedelta(minutes=offset)),
        ).authorize(_principal(ALICE), operation=operation)

    entries = access_log.recent(limit=10)
    # Capacity 2 drops "a", the oldest, and the newest comes first.
    assert [entry.operation for entry in entries] == ["c", "b"]
    assert gate.operator_ids == frozenset({ALICE})


def test_recent_with_a_non_positive_limit_returns_nothing() -> None:
    access_log = InMemoryOperatorAccessLog()
    _gate(operators=frozenset({ALICE}), access_log=access_log).authorize(
        _principal(ALICE), operation="audit-integrity"
    )

    assert access_log.recent(limit=0) == ()


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError):
        InMemoryOperatorAccessLog(capacity=0)
