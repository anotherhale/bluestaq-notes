"""The operator access log survives a restart (NEX-072, closes R42).

R42 is not "the record is stored somewhere inconvenient". It is that a privileged
read recorded in one process did not exist in another: the ring was per-worker and
cleared on restart, so an operator auditing "who read this view" saw only the
worker that answered, and a deploy erased the trail. Both are properties of *two
stores*, so the assertions that matter build a second adapter over the same
database. Asserting against the instance that was written to would pass against
the in-memory ring and prove nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.ports.operator_access import OperatorAccessEntry
from notes_service.domain.identity.value_objects import UserId
from notes_service.infrastructure.db.models import OperatorAccessRow, UserRow
from notes_service.infrastructure.db.operator_access_log import (
    SqlAlchemyOperatorAccessLog,
)
from tests.conftest import ALICE, BOB

pytestmark = pytest.mark.usefixtures("seeded")

NOW = datetime(2026, 11, 27, 17, 0, tzinfo=UTC)


@pytest.fixture
def log(session_factory: sessionmaker[Session]) -> SqlAlchemyOperatorAccessLog:
    return SqlAlchemyOperatorAccessLog(session_factory)


def _restart(session_factory: sessionmaker[Session]) -> SqlAlchemyOperatorAccessLog:
    """A brand-new adapter over the same database — what a restart produces."""
    return SqlAlchemyOperatorAccessLog(session_factory)


def _entry(user: str, operation: str, *, at: datetime = NOW) -> OperatorAccessEntry:
    return OperatorAccessEntry(user_id=UserId.parse(user), operation=operation, at=at)


# --------------------------------------------------------------------------
# Durability: the defect R42 names.
# --------------------------------------------------------------------------


def test_a_read_outlives_the_adapter_that_recorded_it(
    log: SqlAlchemyOperatorAccessLog,
    session_factory: sessionmaker[Session],
) -> None:
    log.record(_entry(ALICE, "withholding-report"))

    restarted = _restart(session_factory)

    entries = restarted.recent(limit=10)
    assert len(entries) == 1
    assert entries[0].user_id == UserId.parse(ALICE)
    assert entries[0].operation == "withholding-report"


def test_two_workers_see_one_anothers_records(session_factory: sessionmaker[Session]) -> None:
    """The failure R40 found in the grant store, checked here before it can recur."""
    worker_a = SqlAlchemyOperatorAccessLog(session_factory)
    worker_b = SqlAlchemyOperatorAccessLog(session_factory)

    worker_a.record(_entry(ALICE, "withholding-report"))
    worker_b.record(_entry(BOB, "audit-integrity"))

    seen_by_b = worker_b.recent(limit=10)
    assert {entry.operation for entry in seen_by_b} == {"withholding-report", "audit-integrity"}


# --------------------------------------------------------------------------
# Ordering: the port promises newest first.
# --------------------------------------------------------------------------


def test_recent_is_newest_first(log: SqlAlchemyOperatorAccessLog) -> None:
    log.record(_entry(ALICE, "first", at=NOW))
    log.record(_entry(ALICE, "second", at=NOW))
    log.record(_entry(ALICE, "third", at=NOW))

    assert [entry.operation for entry in log.recent(limit=10)] == ["third", "second", "first"]


def test_ordering_does_not_depend_on_timestamp_precision(
    log: SqlAlchemyOperatorAccessLog,
) -> None:
    """Two reads sharing an instant must still come back in insertion order.

    This is why ordering is by surrogate ``id`` and not by ``at``: a sort on a
    timestamp both entries share is implementation-defined, and an audit trail
    that can reorder itself is not evidence of sequence.
    """
    same_instant = NOW
    log.record(_entry(ALICE, "alpha", at=same_instant))
    log.record(_entry(BOB, "beta", at=same_instant))

    assert [entry.operation for entry in log.recent(limit=10)] == ["beta", "alpha"]


def test_limit_caps_the_result_and_zero_returns_nothing(
    log: SqlAlchemyOperatorAccessLog,
) -> None:
    for index in range(5):
        log.record(_entry(ALICE, f"op-{index}", at=NOW + timedelta(seconds=index)))

    assert len(log.recent(limit=2)) == 2
    assert log.recent(limit=0) == ()


# --------------------------------------------------------------------------
# Append-only: the record is evidence, so it cannot be rewritten by the adapter.
# --------------------------------------------------------------------------


def test_the_adapter_offers_no_way_to_rewrite_a_record(
    log: SqlAlchemyOperatorAccessLog,
) -> None:
    """A trail the store could edit would not be a trail."""
    for forbidden in ("update", "delete", "remove", "clear"):
        assert not hasattr(log, forbidden)


def test_an_empty_operation_is_refused_by_the_schema(
    log: SqlAlchemyOperatorAccessLog,
) -> None:
    with pytest.raises(IntegrityError):
        log.record(_entry(ALICE, ""))


def test_a_record_survives_the_deletion_of_the_user(
    log: SqlAlchemyOperatorAccessLog,
    session_factory: sessionmaker[Session],
) -> None:
    """Deleting an account must not erase what that account read.

    ``user_id`` is deliberately not a cascading foreign key: an audit record has to
    outlive the subject, or deleting a user becomes a way to destroy the evidence
    of their privileged reads.
    """
    log.record(_entry(ALICE, "withholding-report"))

    with session_factory() as session:
        session.execute(delete(UserRow).where(UserRow.id == uuid.UUID(ALICE)))
        session.commit()

    remaining = _restart(session_factory).recent(limit=10)
    assert [entry.operation for entry in remaining] == ["withholding-report"]


def test_rows_are_physically_present_after_record(
    log: SqlAlchemyOperatorAccessLog,
    session_factory: sessionmaker[Session],
) -> None:
    log.record(_entry(ALICE, "withholding-report"))

    with session_factory() as session:
        rows = session.execute(select(OperatorAccessRow)).scalars().all()

    assert len(rows) == 1
    assert rows[0].operation == "withholding-report"
