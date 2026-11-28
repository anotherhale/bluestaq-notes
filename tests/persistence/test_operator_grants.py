"""The operator grant store survives a restart (NEX-046, closes R40).

The bug R40 records was not "grants are stored somewhere inconvenient" — it was
that a grant issued in one process did not exist in another, so a bounded grant
was forgotten on restart and a grant issued on one worker was invisible to the
rest of a deployment. Both are properties of *two stores*, not one, so the tests
that matter here build a second repository over the same database rather than
re-reading the first one. A test that asserts against the same instance it wrote
to would pass against the in-memory adapter and prove nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, delete, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from notes_service.domain.identity.operator_grant import OperatorGrant, OperatorGrantRationale
from notes_service.domain.identity.value_objects import UserId
from notes_service.infrastructure.db.models import OperatorGrantRow, UserRow
from notes_service.infrastructure.db.operator_grants import (
    SqlAlchemyOperatorGrantRepository,
)
from tests.conftest import ALICE, BOB

pytestmark = pytest.mark.usefixtures("seeded")

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)

REASON = OperatorGrantRationale("covering the incident window")


@pytest.fixture
def grants(session_factory: sessionmaker[Session]) -> SqlAlchemyOperatorGrantRepository:
    return SqlAlchemyOperatorGrantRepository(session_factory)


def _restart(session_factory: sessionmaker[Session]) -> SqlAlchemyOperatorGrantRepository:
    """A brand-new adapter over the same database — what a restart produces."""
    return SqlAlchemyOperatorGrantRepository(session_factory)


def _grant(user: str, scope: str, **overrides: object) -> OperatorGrant:
    return OperatorGrant(
        user_id=UserId.parse(user),
        scope=scope,
        granted_at=NOW,
        **overrides,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------
# Durability: the defect R40 names.
# --------------------------------------------------------------------------


def test_a_grant_outlives_the_adapter_that_wrote_it(
    grants: SqlAlchemyOperatorGrantRepository,
    session_factory: sessionmaker[Session],
) -> None:
    grants.add(_grant(ALICE, "withholding-report"))

    restarted = _restart(session_factory)

    assert restarted.active_for(UserId.parse(ALICE), now=NOW) == frozenset({"withholding-report"})


def test_a_bounded_grant_is_not_forgotten_by_a_restart(
    grants: SqlAlchemyOperatorGrantRepository,
    session_factory: sessionmaker[Session],
) -> None:
    """R40's sharpest case: the grant that expires was the one lost on restart."""
    grants.add(_grant(ALICE, "withholding-report", expires_at=NOW + timedelta(hours=2)))

    restarted = _restart(session_factory)

    assert restarted.active_for(UserId.parse(ALICE), now=NOW) == frozenset({"withholding-report"})
    # And the deadline survived with it, rather than decaying into an unbounded
    # grant — a forgotten deadline is a worse defect than a forgotten grant.
    (only,) = restarted.active_grants(now=NOW)
    assert only.expires_at == NOW + timedelta(hours=2)


def test_a_grant_written_by_one_adapter_is_visible_to_another(
    session_factory: sessionmaker[Session],
) -> None:
    """Two workers, one database. This is the multi-worker half of R40."""
    worker_a = SqlAlchemyOperatorGrantRepository(session_factory)
    worker_b = SqlAlchemyOperatorGrantRepository(session_factory)

    worker_a.add(_grant(BOB, "audit-integrity"))

    assert worker_b.active_for(UserId.parse(BOB), now=NOW) == frozenset({"audit-integrity"})


# --------------------------------------------------------------------------
# The port's semantics, unchanged by the new adapter.
# --------------------------------------------------------------------------


def test_expired_grants_confer_nothing(grants: SqlAlchemyOperatorGrantRepository) -> None:
    grants.add(_grant(ALICE, "withholding-report", expires_at=NOW + timedelta(minutes=1)))

    assert grants.active_for(UserId.parse(ALICE), now=NOW + timedelta(minutes=1)) == frozenset()


def test_a_grant_not_yet_valid_confers_nothing(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    grants.add(_grant(ALICE, "withholding-report", not_before=NOW + timedelta(hours=1)))

    assert grants.active_for(UserId.parse(ALICE), now=NOW) == frozenset()


def test_active_for_is_scoped_to_one_user(grants: SqlAlchemyOperatorGrantRepository) -> None:
    grants.add(_grant(ALICE, "withholding-report"))
    grants.add(_grant(BOB, "audit-integrity"))

    assert grants.active_for(UserId.parse(ALICE), now=NOW) == frozenset({"withholding-report"})
    assert grants.active_for(UserId.parse(BOB), now=NOW) == frozenset({"audit-integrity"})


def test_an_unknown_user_holds_nothing(grants: SqlAlchemyOperatorGrantRepository) -> None:
    assert grants.active_for(UserId.new(), now=NOW) == frozenset()


def test_several_scopes_for_one_operator(grants: SqlAlchemyOperatorGrantRepository) -> None:
    grants.add(_grant(ALICE, "withholding-report"))
    grants.add(_grant(ALICE, "audit-integrity"))

    assert grants.active_for(UserId.parse(ALICE), now=NOW) == frozenset(
        {"withholding-report", "audit-integrity"}
    )


def test_active_grants_is_in_insertion_order(grants: SqlAlchemyOperatorGrantRepository) -> None:
    """Stable ordering is what makes the report diffable between runs."""
    grants.add(_grant(ALICE, "withholding-report"))
    grants.add(_grant(BOB, "audit-integrity"))
    grants.add(_grant(ALICE, "operator-access-report"))

    holders = [(str(g.user_id), g.scope) for g in grants.active_grants(now=NOW)]

    assert holders == [
        (ALICE, "withholding-report"),
        (BOB, "audit-integrity"),
        (ALICE, "operator-access-report"),
    ]


def test_active_grants_omits_inactive_ones(grants: SqlAlchemyOperatorGrantRepository) -> None:
    grants.add(_grant(ALICE, "withholding-report"))
    grants.add(_grant(BOB, "audit-integrity", expires_at=NOW + timedelta(seconds=1)))

    holders = [g.scope for g in grants.active_grants(now=NOW + timedelta(seconds=1))]

    assert holders == ["withholding-report"]


def test_the_adapter_reports_its_name(grants: SqlAlchemyOperatorGrantRepository) -> None:
    assert grants.name == "sqlalchemy"


def test_adding_something_that_is_not_a_grant_is_refused(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    with pytest.raises(TypeError):
        grants.add("alice:withholding-report")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Idempotency: a seed that runs on every boot must not accumulate rows.
# --------------------------------------------------------------------------


def test_adding_the_same_grant_twice_stores_it_once(
    grants: SqlAlchemyOperatorGrantRepository, engine: Engine
) -> None:
    grants.add(_grant(ALICE, "withholding-report"))
    grants.add(_grant(ALICE, "withholding-report"))

    with Session(engine) as session:
        assert session.execute(select(OperatorGrantRow)).scalars().all().__len__() == 1


def test_re_issuing_a_grant_extends_its_deadline_rather_than_duplicating(
    grants: SqlAlchemyOperatorGrantRepository, engine: Engine
) -> None:
    """Same grant identity, later deadline: an extension, not a second grant."""
    grants.add(_grant(ALICE, "withholding-report"))
    grants.add(_grant(ALICE, "withholding-report", expires_at=NOW + timedelta(hours=4)))

    with Session(engine) as session:
        rows = session.execute(select(OperatorGrantRow)).scalars().all()

    assert len(rows) == 1
    assert rows[0].expires_at == NOW + timedelta(hours=4)


def test_a_human_issued_grant_records_who_issued_it_and_why(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    grants.add(_grant(ALICE, "withholding-report", granted_by=UserId.parse(BOB), rationale=REASON))

    (only,) = grants.active_grants(now=NOW)
    assert only.granted_by == UserId.parse(BOB)
    assert only.rationale == REASON


def test_the_rationale_survives_a_restart(
    grants: SqlAlchemyOperatorGrantRepository,
    session_factory: sessionmaker[Session],
) -> None:
    """The reason is in the row, not only in the writer's memory.

    Read back through a second adapter over the same database, so this fails if
    the column is dropped from either the write or the read mapping.
    """
    grants.add(_grant(ALICE, "withholding-report", granted_by=UserId.parse(BOB), rationale=REASON))

    (reloaded,) = _restart(session_factory).active_grants(now=NOW)
    assert reloaded.rationale == REASON
    assert reloaded.rationale is not None and reloaded.rationale.value == REASON.value


def test_the_rationale_column_cannot_hold_an_empty_string(
    grants: SqlAlchemyOperatorGrantRepository, engine: Engine
) -> None:
    """A check constraint, so a row cannot exist without a stated reason."""
    with pytest.raises(IntegrityError), Session(engine) as session:
        session.add(
            OperatorGrantRow(
                user_id=UserId(ALICE),
                scope="withholding-report",
                granted_at=NOW,
                rationale="",
            )
        )
        session.commit()


# --------------------------------------------------------------------------
# The seed path: replace what configuration issued, keep what a human issued.
# --------------------------------------------------------------------------


def test_replace_configured_is_idempotent_across_restarts(
    grants: SqlAlchemyOperatorGrantRepository,
    session_factory: sessionmaker[Session],
) -> None:
    """Boot twice with the same configuration; the store must not double."""
    config = [_grant(ALICE, "withholding-report"), _grant(BOB, "audit-integrity")]

    grants.replace_configured(config)
    later = NOW + timedelta(days=1)
    _restart(session_factory).replace_configured(
        [_grant(ALICE, "withholding-report"), _grant(BOB, "audit-integrity")]
    )

    assert len(grants.active_grants(now=later)) == 2


def test_replace_configured_drops_a_scope_removed_from_configuration(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    """Removing an operator from the variable must actually revoke the grant."""
    grants.replace_configured([_grant(ALICE, "withholding-report")])
    grants.replace_configured([])

    assert grants.active_for(UserId.parse(ALICE), now=NOW) == frozenset()


def test_replace_configured_leaves_human_issued_grants_alone(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    """A redeploy must not erase a bounded grant someone issued for an incident."""
    grants.add(
        _grant(
            ALICE,
            "audit-integrity",
            granted_by=UserId.parse(BOB),
            rationale=REASON,
            expires_at=NOW + timedelta(hours=1),
        )
    )

    grants.replace_configured([_grant(BOB, "withholding-report")])

    holders = {(str(g.user_id), g.scope) for g in grants.active_grants(now=NOW)}
    assert holders == {(ALICE, "audit-integrity"), (BOB, "withholding-report")}


def test_replace_configured_refuses_a_grant_it_does_not_own(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    with pytest.raises(ValueError):
        grants.replace_configured(
            [_grant(ALICE, "withholding-report", granted_by=UserId.parse(BOB), rationale=REASON)]
        )


# --------------------------------------------------------------------------
# Constraints.
# --------------------------------------------------------------------------


def test_a_grant_for_an_unknown_user_is_refused_by_the_foreign_key(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    with pytest.raises(Exception) as excinfo:
        grants.add(_grant(str(UserId.new()), "withholding-report"))
    assert "FOREIGN KEY" in str(excinfo.value).upper() or "foreign key" in str(excinfo.value)


def test_the_scope_column_cannot_hold_an_empty_string(
    grants: SqlAlchemyOperatorGrantRepository, engine: Engine
) -> None:
    with pytest.raises(IntegrityError), Session(engine) as session:
        session.add(
            OperatorGrantRow(
                user_id=UserId(ALICE),
                scope="",
                granted_at=NOW,
            )
        )
        session.commit()


def test_deleting_a_user_removes_their_grants(
    grants: SqlAlchemyOperatorGrantRepository, engine: Engine, seeded: None
) -> None:
    """CASCADE, not orphan rows: a deleted operator must not leave a live grant."""
    grants.add(_grant(ALICE, "withholding-report"))

    with Session(engine) as session:
        session.execute(delete(UserRow).where(UserRow.id == uuid.UUID(ALICE)))
        session.commit()

    with Session(engine) as session:
        assert session.execute(select(OperatorGrantRow)).scalars().all() == []


def test_the_table_exists_in_the_migrated_schema(engine: Engine) -> None:
    """The ORM model and the migration must not disagree about the table."""
    columns = {column["name"] for column in inspect(engine).get_columns("operator_grants")}
    assert columns == {
        "id",
        "user_id",
        "scope",
        "granted_at",
        "granted_by",
        "not_before",
        "expires_at",
        "rationale",
        # NEX-079: a revocation is a recorded fact on the row, not a deletion.
        "revoked_at",
        "revoked_by",
    }
