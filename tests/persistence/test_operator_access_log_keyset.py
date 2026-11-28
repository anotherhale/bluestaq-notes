"""Both operator access log adapters honour the keyset cursor (NEX-082).

The port's ``recent(before_position=...)`` is what makes the access report
pageable. These tests exercise the two adapters directly, because the property
that matters — "strictly older, no gap, no overlap" — can only be checked against
a store that actually holds more entries than one page, and the API tests would
take hundreds of requests to reach that state.

The in-memory ring is tested for a subtle failure the durable store cannot have:
its capacity is fixed, so once it is full every ``record`` also drops an entry.
A position derived from ``len(self._entries)`` would therefore *reuse* an
ordinate that a surviving entry already holds, and a keyset cursor built on it
would skip rows. The counter has to be monotonic, and this pins it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.ports.operator_access import OperatorAccessEntry
from notes_service.infrastructure.db.operator_access_log import (
    SqlAlchemyOperatorAccessLog,
)
from notes_service.infrastructure.security.operator_access_log import (
    InMemoryOperatorAccessLog,
)
from tests.conftest import ALICE

pytestmark = pytest.mark.usefixtures("seeded")

NOW = datetime(2026, 11, 27, 17, 0, tzinfo=UTC)


def _entry(operation: str) -> OperatorAccessEntry:
    return OperatorAccessEntry(user_id=ALICE, operation=operation, at=NOW)


@pytest.fixture
def sql_log(session_factory: sessionmaker[Session]) -> SqlAlchemyOperatorAccessLog:
    return SqlAlchemyOperatorAccessLog(session_factory)


@pytest.fixture(params=["in-memory", "sql"])
def any_log(request: pytest.FixtureRequest, session_factory: sessionmaker[Session]):
    if request.param == "in-memory":
        return InMemoryOperatorAccessLog(capacity=100)
    return SqlAlchemyOperatorAccessLog(session_factory)


# --------------------------------------------------------------------------
# Positions are handed out, monotonic, and unique.
# --------------------------------------------------------------------------


def test_every_entry_names_its_position(any_log) -> None:
    """A cursor needs an address; an entry without one cannot be continued from."""
    for operation in ("one", "two", "three"):
        any_log.record(_entry(operation))

    entries = any_log.recent(limit=10)
    assert all(entry.position is not None for entry in entries)
    assert len({entry.position for entry in entries}) == len(entries)


def test_positions_ascend_with_insertion(any_log) -> None:
    for operation in ("one", "two", "three"):
        any_log.record(_entry(operation))

    newest_first = any_log.recent(limit=10)
    positions = [entry.position for entry in newest_first]
    assert positions == sorted(positions, reverse=True)


def test_the_in_memory_ring_respects_a_supplied_position() -> None:
    """The ring stores whatever position it is handed, so a caller can pre-address."""
    log = InMemoryOperatorAccessLog(capacity=10)
    log.record(OperatorAccessEntry(user_id=ALICE, operation="pinned", at=NOW, position=9001))

    assert log.recent(limit=1)[0].position == 9001
    # The counter must not hand 9001 out again to the next entry.
    log.record(_entry("next"))
    assert log.recent(limit=1)[0].position == 9002


def test_the_durable_store_assigns_its_own_position(
    sql_log: SqlAlchemyOperatorAccessLog,
) -> None:
    """The table's identity column is the position, so a supplied one is ignored.

    This is the durable adapter's contract, not a defect: the ordinal has to be
    database-assigned for it to be total across processes, and a caller-supplied
    value could collide with a row another worker already wrote.
    """
    sql_log.record(OperatorAccessEntry(user_id=ALICE, operation="pinned", at=NOW, position=9001))

    position = sql_log.recent(limit=1)[0].position
    assert position is not None
    assert position != 9001


# --------------------------------------------------------------------------
# The cursor selects strictly older entries.
# --------------------------------------------------------------------------


def test_the_cursor_returns_only_strictly_older_entries(any_log) -> None:
    for operation in ("one", "two", "three", "four"):
        any_log.record(_entry(operation))

    newest = any_log.recent(limit=1)[0]
    older = any_log.recent(limit=10, before_position=newest.position)

    assert [entry.operation for entry in older] == ["three", "two", "one"]
    assert all(entry.position < newest.position for entry in older)


def test_a_cursor_below_every_position_returns_nothing(any_log) -> None:
    """The end of the trail is empty, not a wrap-around to the newest entries."""
    for operation in ("one", "two"):
        any_log.record(_entry(operation))

    assert any_log.recent(limit=10, before_position=1) == ()


def test_a_cursor_above_every_position_returns_the_head(any_log) -> None:
    for operation in ("one", "two"):
        any_log.record(_entry(operation))

    assert len(any_log.recent(limit=10, before_position=10_000)) == 2


def test_paging_with_the_cursor_visits_each_entry_once(any_log) -> None:
    """Walk the whole log two at a time and assert it is exactly the log."""
    for index in range(7):
        any_log.record(_entry(f"op-{index}"))

    seen: list[str] = []
    cursor: int | None = None
    for _ in range(50):  # a guard, not the expected iteration count
        page = any_log.recent(limit=2, before_position=cursor)
        if not page:
            break
        seen.extend(entry.operation for entry in page)
        cursor = page[-1].position
    else:  # pragma: no cover - would mean the walk never terminated
        pytest.fail("paging did not terminate")

    assert seen == [f"op-{index}" for index in reversed(range(7))]


# --------------------------------------------------------------------------
# The in-memory ring's position counter must survive eviction.
# --------------------------------------------------------------------------


def test_the_ring_reuses_no_position_after_eviction() -> None:
    """A length-derived position would collide with a surviving entry.

    The ring is capped at two, so recording a third entry evicts the first. If the
    position were ``len(self._entries)``, the third entry would be handed the same
    position the second already holds, and a cursor taken from the second would
    then hide the third — a silent gap in an audit trail.
    """
    log = InMemoryOperatorAccessLog(capacity=2)
    log.record(_entry("one"))
    log.record(_entry("two"))
    log.record(_entry("three"))

    positions = [entry.position for entry in log.recent(limit=10)]
    assert len(positions) == len(set(positions)), f"positions collided: {positions}"
    assert positions == sorted(positions, reverse=True)

    newest = log.recent(limit=1)[0]
    older = log.recent(limit=10, before_position=newest.position)
    assert [entry.operation for entry in older] == ["two"]
