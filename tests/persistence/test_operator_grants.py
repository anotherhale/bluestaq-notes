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
from sqlalchemy import Engine, delete, event, inspect, select
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
        **{"granted_at": NOW, **overrides},  # type: ignore[arg-type]
    )


def _lapsed(user: str, scope: str, *, before: datetime) -> OperatorGrant:
    """A grant whose window closed before ``before`` — expired, not revoked.

    The domain refuses a grant that expires at or before it was issued, so the
    window has to be a real one: issued, then closed. The distinction matters here,
    because ``revoked_at`` and ``expires_at`` reach the active predicate by different
    clauses and only one of them is clock-relative.
    """
    return _grant(
        user,
        scope,
        granted_at=before - timedelta(hours=1),
        expires_at=before - timedelta(minutes=1),
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


# -- revocation history: the read half of NEX-079 (NEX-080) -----------------


def _revoke(
    grants: SqlAlchemyOperatorGrantRepository,
    user: str,
    scope: str = "withholding-report",
    *,
    by: str = ALICE,
    at: datetime = NOW,
) -> int:
    return grants.revoke(UserId.parse(user), scope, at=at, by=UserId.parse(by))


def test_revoked_grants_reports_a_revocation_with_its_actor(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    """What NEX-079 wrote, NEX-080 now reads back: who, and when."""
    grants.add(_grant(BOB, "withholding-report"))
    assert _revoke(grants, BOB) == 1

    (row,) = grants.revoked_grants()
    assert str(row.user_id) == BOB
    assert row.scope == "withholding-report"
    assert row.revoked_at == NOW
    assert str(row.revoked_by) == ALICE
    assert row.is_revoked


def test_revoked_grants_omits_live_and_merely_expired_grants(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    """Lapsing is not revocation: only a stamped revocation is history."""
    grants.add(_grant(ALICE, "withholding-report"))
    grants.add(_grant(BOB, "audit-integrity", expires_at=NOW + timedelta(seconds=1)))

    # The bound has passed, so BOB's grant confers nothing — but nobody revoked it.
    live = [row.scope for row in grants.active_grants(now=NOW + timedelta(seconds=5))]
    assert live == ["withholding-report"]
    assert grants.revoked_grants() == ()


def test_a_revocation_outlives_a_deleted_revoker(
    grants: SqlAlchemyOperatorGrantRepository, engine: Engine, seeded: None
) -> None:
    """The migration's whole point: deleting the revoker must not break or erase it.

    Before NEX-080 the CHECK required both revocation columns together, so the
    ``SET NULL`` on ``revoked_by`` made the delete itself fail with a raw
    ``IntegrityError`` — no account holding revocation history could be deleted at
    all. Now the delete succeeds, the revocation survives, and the actor is NULL.
    """
    grants.add(_grant(BOB, "withholding-report"))
    assert _revoke(grants, BOB) == 1

    with Session(engine) as session:
        session.execute(delete(UserRow).where(UserRow.id == uuid.UUID(ALICE)))
        session.commit()

    (row,) = grants.revoked_grants()
    assert str(row.user_id) == BOB
    assert row.revoked_at == NOW
    assert row.revoked_by is None
    # Still revoked: the authority must not come back because its revoker left.
    assert row.is_revoked


def test_deleting_an_issuer_leaves_every_read_working(
    grants: SqlAlchemyOperatorGrantRepository, engine: Engine, seeded: None
) -> None:
    """A deleted issuer must not turn the gate lookup into a 500.

    ``granted_by`` is ``SET NULL`` while ``rationale`` survives, so the row's issuer
    is gone but its reason is not. The domain previously rejected that shape, which
    made ``active_grants`` — and therefore the operator gate — raise on any read
    once an issuer was deleted. That was a self-inflicted outage of ``/ops``.
    """
    grants.add(_grant(BOB, "withholding-report", granted_by=UserId.parse(ALICE), rationale=REASON))

    with Session(engine) as session:
        session.execute(delete(UserRow).where(UserRow.id == uuid.UUID(ALICE)))
        session.commit()

    (row,) = grants.active_grants(now=NOW)
    assert row.rationale is not None
    assert row.granted_by is None


def test_revoked_grants_survives_a_restart(
    grants: SqlAlchemyOperatorGrantRepository, session_factory: sessionmaker[Session]
) -> None:
    """A second adapter over the same database sees the same history."""
    grants.add(_grant(BOB, "withholding-report"))
    assert _revoke(grants, BOB) == 1

    (row,) = _restart(session_factory).revoked_grants()
    assert str(row.user_id) == BOB
    assert str(row.revoked_by) == ALICE


def test_revoked_grants_is_in_insertion_order(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    grants.add(_grant(ALICE, "withholding-report"))
    grants.add(_grant(BOB, "audit-integrity"))
    _revoke(grants, ALICE)
    _revoke(grants, BOB, scope="audit-integrity")

    scopes = [row.scope for row in grants.revoked_grants()]
    assert scopes == ["withholding-report", "audit-integrity"]


def test_revoked_grants_pages_with_limit_and_offset(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    """Paging (NEX-086): the adapter slices the same stable order it returns whole.

    ``revoked_grants`` is the unbounded read the report used to make; with paging
    it must agree with itself — a page at each offset, joined, is the whole list,
    in order, no gaps and no repeats. ``count_revoked`` must equal that list's
    length, or the report's ``next_offset`` would point past or short of the end.
    """
    grants.add(_grant(ALICE, "withholding-report"))
    grants.add(_grant(BOB, "audit-integrity"))
    _revoke(grants, ALICE)
    _revoke(grants, BOB, scope="audit-integrity")

    whole = grants.revoked_grants()
    assert [row.scope for row in whole] == ["withholding-report", "audit-integrity"]
    assert grants.count_revoked() == 2

    first = grants.revoked_grants(limit=1, offset=0)
    second = grants.revoked_grants(limit=1, offset=1)
    assert [row.scope for row in first + second] == [row.scope for row in whole]
    assert grants.revoked_grants(limit=2, offset=2) == ()
    assert grants.revoked_grants(limit=None, offset=1) == whole[1:]


def test_a_page_does_not_read_the_whole_grant_table(
    grants: SqlAlchemyOperatorGrantRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NEX-092: the bound is in the store, not a slice of a materialised table.

    NEX-086 reported this read as bounded and it was not. ``select(OperatorGrantRow)``
    carried no ``WHERE``: every grant row came back, a domain object was built for
    each, and only the resulting list was sliced. So a page of five cost the whole
    table, and the cost grew with the deployment's total revocation history — the
    unbounded-response defect NEX-087 and NEX-088 fixed elsewhere, one layer down.

    Asserted on work performed rather than on generated SQL, so a harmless change of
    clause order or SQLAlchemy's rendering cannot fail it while a regression to
    in-Python selection cannot pass it. ``_to_domain`` is the adapter's only way to
    turn a row into a domain object, so counting its calls counts the rows the
    adapter actually read, whether or not the store returned more.
    """
    from notes_service.infrastructure.db import operator_grants as adapter

    converted: list[str] = []
    real_to_domain = adapter._to_domain

    def counting(row: OperatorGrantRow) -> OperatorGrant:
        converted.append(row.scope)
        return real_to_domain(row)

    monkeypatch.setattr(adapter, "_to_domain", counting)

    revoked_scopes = [f"scope-{index}" for index in range(40)]
    live_scopes = [f"live-{index}" for index in range(40)]
    for scope in revoked_scopes:
        grants.add(_grant(ALICE, scope))
        _revoke(grants, ALICE, scope=scope)
    for scope in live_scopes:
        grants.add(_grant(BOB, scope))

    converted.clear()
    page = grants.revoked_grants(limit=5, offset=0)

    # Five rows read, five objects built — not eighty rows filtered down to five.
    assert [row.scope for row in page] == revoked_scopes[:5]
    assert len(converted) == 5

    converted.clear()
    second = grants.revoked_grants(limit=5, offset=5)
    assert [row.scope for row in second] == revoked_scopes[5:10]
    assert len(converted) == 5

    # The total is an aggregate: it must not convert a single row to answer.
    converted.clear()
    assert grants.count_revoked() == len(revoked_scopes)
    assert converted == []


def test_count_revoked_omits_live_and_merely_expired_grants(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    """The total is revoked facts only, matching what the list would return.

    A cheaper ``revoked_at IS NOT NULL`` count is tempting, but a grant that merely
    lapsed must not inflate the total: the count and the page have to describe the
    same set, or ``next_offset`` lies about where the history ends.
    """
    grants.add(_grant(ALICE, "withholding-report"))
    grants.add(_grant(BOB, "audit-integrity", expires_at=NOW + timedelta(seconds=1)))
    _revoke(grants, ALICE)

    assert grants.count_revoked() == 1
    assert len(grants.revoked_grants()) == 1


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


# -- the active reads are bounded in the store (NEX-094) ----------------------


def _counting_to_domain(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count the rows the adapter turns into domain objects.

    ``_to_domain`` is the adapter's only path from row to domain object, so counting
    its calls counts the rows the store actually handed back — whether or not the
    caller needed them.
    """
    from notes_service.infrastructure.db import operator_grants as adapter

    converted: list[str] = []
    real_to_domain = adapter._to_domain

    def counting(row: OperatorGrantRow) -> OperatorGrant:
        converted.append(row.scope)
        return real_to_domain(row)

    monkeypatch.setattr(adapter, "_to_domain", counting)
    return converted


def test_active_grants_does_not_read_the_whole_grant_table(
    grants: SqlAlchemyOperatorGrantRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NEX-094: the bound is a ``WHERE``, not a filter over a materialised table.

    The active reads selected every grant row and discarded most in Python. Unlike
    the revocation history, the rows they discard are never pruned — the table keeps
    revoked and expired grants on purpose — so the cost grew with every grant the
    deployment had ever issued. Asserted on work performed rather than on rendered
    SQL, so clause reordering cannot fail it while a regression to in-Python
    filtering cannot pass it.
    """
    converted = _counting_to_domain(monkeypatch)

    for index in range(40):
        scope = f"revoked-{index}"
        grants.add(_grant(BOB, scope))
        _revoke(grants, BOB, scope=scope)
    for index in range(20):
        grants.add(_lapsed(ALICE, f"lapsed-{index}", before=NOW))
    live = [f"live-{index}" for index in range(3)]
    for scope in live:
        grants.add(_grant(ALICE, scope))

    converted.clear()
    holders = grants.active_grants(now=NOW)

    # Three live rows read and built — not sixty-three rows filtered down to three.
    assert [grant.scope for grant in holders] == live
    assert sorted(converted) == sorted(live)


def test_active_reads_filter_in_the_store_rather_than_in_python(engine: Engine) -> None:
    """Both active reads must carry the bounds as a ``WHERE``, not a Python filter.

    ``active_for`` builds its answer straight from ``row.scope``, so there is no
    per-row conversion to count — the statement it sends is the only evidence of
    where the work happens. Asserted on the emitted SQL rather than on the adapter's
    source, so it holds however the statement is composed.

    The table only grows: revoked and expired grants are kept on purpose, so a read
    that omits the filter costs the whole grant history forever.
    """
    seen: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        if "operator_grants" in statement:
            seen.append(" ".join(statement.lower().split()))

    event.listen(engine, "before_cursor_execute", record)
    try:
        grants = SqlAlchemyOperatorGrantRepository(sessionmaker(bind=engine))
        grants.add(_grant(ALICE, "scope-read"))
        seen.clear()
        grants.active_grants(now=NOW)
        grants.active_for(UserId.parse(ALICE), now=NOW)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    selects = [sql for sql in seen if sql.lstrip().startswith("select")]
    assert len(selects) == 2, seen
    for sql in selects:
        assert "revoked_at is null" in sql, sql
        assert "expires_at" in sql, sql


def _boundary_grants(instant: datetime) -> list[tuple[str, dict[str, object]]]:
    """One grant per comparison in ``is_active``, built around ``instant``.

    Each case sits exactly on, or one second either side of, one boundary the
    domain rule tests, so a SQL predicate that used ``<`` where the domain says
    ``<=`` disagrees here rather than silently admitting or dropping a grant from
    an operator read. ``granted_at`` is backdated where a case needs an earlier
    bound, because the domain refuses a grant that expires at or before issue.
    """
    hour = timedelta(hours=1)
    second = timedelta(seconds=1)
    cases: list[tuple[str, dict[str, object]]] = [
        ("bare", {"granted_at": instant - hour}),
        ("expiry-at", {"granted_at": instant - hour, "expires_at": instant}),
        ("expiry-after", {"granted_at": instant - hour, "expires_at": instant + second}),
        ("expiry-before", {"granted_at": instant - 2 * hour, "expires_at": instant - second}),
        (
            "not-before-at",
            {"granted_at": instant - hour, "not_before": instant, "expires_at": instant + hour},
        ),
        (
            "not-before-after",
            {
                "granted_at": instant - hour,
                "not_before": instant + second,
                "expires_at": instant + hour,
            },
        ),
        (
            "not-before-before",
            {"not_before": instant - second, "expires_at": instant + hour},
        ),
        ("granted-at-instant", {"granted_at": instant}),
        ("granted-later", {"granted_at": instant + second}),
        (
            "window-straddling",
            {"granted_at": instant - hour, "not_before": instant - hour, "expires_at": instant},
        ),
    ]
    return cases


@pytest.mark.parametrize(
    "instant",
    [NOW - timedelta(seconds=1), NOW, NOW + timedelta(seconds=1)],
    ids=["before", "at", "after"],
)
def test_active_predicate_agrees_with_the_domain(
    grants: SqlAlchemyOperatorGrantRepository, instant: datetime
) -> None:
    """The SQL predicate and ``is_active`` must select exactly the same grant set.

    NEX-094 moved the "in force" decision into the store, which is only safe if the
    translation is exact — including how the bound values are written and compared,
    which is why a boundary case is evaluated at an instant it actually coincides
    with. A revoked row is included too, since no clock may resurrect it.
    """
    on_domain_side: list[str] = []
    for scope, overrides in _boundary_grants(instant):
        grant = _grant(ALICE, scope, **overrides)
        grants.add(grant)
        if grant.is_active(now=instant):
            on_domain_side.append(scope)

    grants.add(_grant(ALICE, "revoked"))
    # Revoked at exactly ``granted_at``: ``revoke`` refuses a grant that is not yet
    # active at the instant given, so this is the earliest revocation it will record.
    _revoke(grants, ALICE, scope="revoked", at=instant)

    in_store = [grant.scope for grant in grants.active_grants(now=instant)]

    assert in_store == on_domain_side
    assert "revoked" not in in_store
    # Guard against a vacuous pass: the case set must contain both verdicts.
    assert len(on_domain_side) < len(_boundary_grants(instant))


def test_a_revoked_grant_is_excluded_even_when_its_other_bounds_are_open(
    grants: SqlAlchemyOperatorGrantRepository,
) -> None:
    """The one branch of ``is_active`` that ignores the clock must survive the move.

    Revocation is a decision, not a time bound, so ``revoked_at IS NULL`` is not
    optional in the predicate. Dropping it would leave a grant with no expiry open
    to anyone at any clock value — the fail-open the delegation model exists to
    prevent — and the grant here is revoked with a *future* instant, so a predicate
    that compared ``revoked_at`` to ``now`` rather than to NULL would also admit it.
    """
    grants.add(_grant(ALICE, "withholding-report"))
    _revoke(grants, ALICE, scope="withholding-report", at=NOW + timedelta(days=1))

    assert grants.active_for(UserId.parse(ALICE), now=NOW) == frozenset()
    assert grants.active_grants(now=NOW) == ()
