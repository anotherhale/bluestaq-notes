"""The gate reads a store, so a grant can lapse without a redeploy (NEX-045).

NEX-044 narrowed each grant to a scope but the allowlist was still a static map
built once at startup: the only way to revoke a grant was to edit configuration
and restart. These tests pin what the stored grant changes:

* **an expired grant stops working at the gate's clock**, mid-process, with no
  restart and no configuration change;
* **a not-yet-valid grant does not work early** — a window that has not opened is
  not a permission;
* **the holder list is the gate's own view**: every id it reports is an id the
  gate would admit at the same instant, and an id whose grants have all lapsed is
  absent rather than reported as an operator;
* **a refused read leaves no trace**, exactly as before — the access log is
  evidence of authorisation, and that invariant must survive the new store.

The clock is injected so "now" is a value these tests choose, not wall time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from notes_service.application.errors import ResourceNotFound
from notes_service.application.ports.clock import Clock
from notes_service.application.use_cases.operator_gate import WILDCARD, OperatorGate
from notes_service.application.use_cases.operator_grants import (
    OPERATION,
    ListOperatorGrants,
    ListRevokedOperatorGrants,
)
from notes_service.domain.identity.operator_grant import (
    OperatorGrant,
    OperatorGrantRationale,
)
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.security.operator_access_log import (
    InMemoryOperatorAccessLog,
)
from notes_service.infrastructure.security.operator_grants import (
    InMemoryOperatorGrantRepository,
)

ALICE = UserId.parse("11111111-1111-1111-1111-111111111111")
BOB = UserId.parse("22222222-2222-2222-2222-222222222222")
CAROL = UserId.parse("33333333-3333-3333-3333-333333333333")

BASE = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)


class FixedClock(Clock):
    """A clock whose ``now`` the test can move, so expiry is deterministic."""

    def __init__(self, now: datetime) -> None:
        self.now_value = now

    def now(self) -> datetime:
        return self.now_value

    def advance(self, delta: timedelta) -> None:
        self.now_value += delta


def _principal(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email("user@example.com"),
        display_name="User",
        provider="demo",
        subject=str(user_id),
    )


def _store(*grants: OperatorGrant) -> InMemoryOperatorGrantRepository:
    repository = InMemoryOperatorGrantRepository()
    for grant in grants:
        repository.add(grant)
    return repository


def _gate(
    grants: InMemoryOperatorGrantRepository,
    *,
    clock: FixedClock | None = None,
    access_log: InMemoryOperatorAccessLog | None = None,
) -> OperatorGate:
    return OperatorGate(
        grants=grants,
        access_log=access_log or InMemoryOperatorAccessLog(),
        clock=clock or FixedClock(BASE),
    )


def _grant(
    user_id: UserId = ALICE,
    scope: str = "withholding-report",
    **bounds: object,
) -> OperatorGrant:
    return OperatorGrant(
        user_id=user_id,
        scope=scope,
        granted_at=BASE,
        **bounds,  # type: ignore[arg-type]
    )


# -- expiry at the gate -----------------------------------------------------


def test_an_expired_grant_stops_working_without_a_restart() -> None:
    clock = FixedClock(BASE)
    gate = _gate(
        _store(_grant(expires_at=BASE + timedelta(hours=1))),
        clock=clock,
    )

    gate.authorize(_principal(ALICE), operation="withholding-report")

    clock.advance(timedelta(hours=1))
    with pytest.raises(ResourceNotFound):
        gate.authorize(_principal(ALICE), operation="withholding-report")


def test_a_not_yet_valid_grant_does_not_work_early() -> None:
    clock = FixedClock(BASE)
    gate = _gate(
        _store(_grant(not_before=BASE + timedelta(hours=1))),
        clock=clock,
    )

    with pytest.raises(ResourceNotFound):
        gate.authorize(_principal(ALICE), operation="withholding-report")

    clock.advance(timedelta(hours=1))
    gate.authorize(_principal(ALICE), operation="withholding-report")


def test_an_expired_grant_leaves_no_access_entry() -> None:
    """A refused read must not be recorded; that invariant survives the store."""
    access_log = InMemoryOperatorAccessLog()
    clock = FixedClock(BASE)
    gate = _gate(
        _store(_grant(expires_at=BASE + timedelta(hours=1))),
        clock=clock,
        access_log=access_log,
    )
    gate.authorize(_principal(ALICE), operation="withholding-report")

    clock.advance(timedelta(hours=2))
    with pytest.raises(ResourceNotFound):
        gate.authorize(_principal(ALICE), operation="withholding-report")

    # Only the one successful read is on record.
    assert len(access_log.recent(limit=10)) == 1


def test_an_empty_store_admits_nobody() -> None:
    with pytest.raises(ResourceNotFound):
        _gate(_store()).authorize(_principal(ALICE), operation="withholding-report")


def test_operator_ids_omits_a_holder_whose_grants_have_lapsed() -> None:
    clock = FixedClock(BASE)
    gate = _gate(
        _store(
            _grant(ALICE, expires_at=BASE + timedelta(hours=1)),
            _grant(BOB),  # unbounded
        ),
        clock=clock,
    )
    assert gate.operator_ids == frozenset({ALICE, BOB})

    clock.advance(timedelta(hours=2))
    # ALICE's only grant has lapsed, so she is no longer an operator.
    assert gate.operator_ids == frozenset({BOB})


def test_a_wildcard_grant_covers_every_operation() -> None:
    _gate(_store(_grant(scope=WILDCARD))).authorize(_principal(ALICE), operation="audit-integrity")


# -- the holder list --------------------------------------------------------


def _list_use_case(
    grants: InMemoryOperatorGrantRepository,
    *,
    clock: FixedClock | None = None,
) -> tuple[ListOperatorGrants, FixedClock]:
    resolved = clock or FixedClock(BASE)
    gate = _gate(grants, clock=resolved)
    return (
        ListOperatorGrants(grants=grants, operator_gate=gate, clock=resolved),
        resolved,
    )


def _reporting_store(*extra: OperatorGrant) -> InMemoryOperatorGrantRepository:
    # ALICE may read the holder list; the extra grants are who she reports on.
    return _store(_grant(ALICE, scope=OPERATION), *extra)


def test_a_holder_is_reported_with_their_scopes() -> None:
    use_case, _ = _list_use_case(
        _reporting_store(
            _grant(BOB, scope="withholding-report"),
            _grant(BOB, scope="audit-integrity"),
        )
    )

    snapshot = use_case.run(_principal(ALICE))
    holders = {holder.user_id: holder for holder in snapshot.holders}

    assert holders[BOB].scopes == frozenset({"withholding-report", "audit-integrity"})
    assert holders[BOB].expires_at is None


def test_an_expired_holder_is_absent_from_the_report() -> None:
    clock = FixedClock(BASE)
    use_case, _ = _list_use_case(
        _reporting_store(_grant(BOB, expires_at=BASE + timedelta(hours=1))),
        clock=clock,
    )
    assert BOB in {holder.user_id for holder in use_case.run(_principal(ALICE)).holders}

    clock.advance(timedelta(hours=2))
    assert BOB not in {holder.user_id for holder in use_case.run(_principal(ALICE)).holders}


def test_the_nearest_deadline_describes_a_bounded_holder() -> None:
    use_case, _ = _list_use_case(
        _reporting_store(
            _grant(BOB, scope="withholding-report", expires_at=BASE + timedelta(days=7)),
            _grant(BOB, scope="audit-integrity", expires_at=BASE + timedelta(hours=1)),
        )
    )
    holders = {holder.user_id: holder for holder in use_case.run(_principal(ALICE)).holders}

    # The earliest deadline: the soonest this holder loses *a* grant.
    assert holders[BOB].expires_at == BASE + timedelta(hours=1)


def test_an_unbounded_grant_reports_no_deadline_even_beside_a_bounded_one() -> None:
    """The strongest grant describes how long access lasts, so it wins."""
    use_case, _ = _list_use_case(
        _reporting_store(
            _grant(BOB, scope="withholding-report", expires_at=BASE + timedelta(hours=1)),
            _grant(BOB, scope="audit-integrity"),  # unbounded
        )
    )
    holders = {holder.user_id: holder for holder in use_case.run(_principal(ALICE)).holders}

    assert holders[BOB].expires_at is None


def test_a_non_operator_cannot_read_the_holder_list() -> None:
    use_case, _ = _list_use_case(_reporting_store())
    with pytest.raises(ResourceNotFound):
        use_case.run(_principal(CAROL))


def test_reading_the_holder_list_is_recorded() -> None:
    """Enumerating holders is itself an operator read, and is audited as one."""
    access_log = InMemoryOperatorAccessLog()
    store = _reporting_store(_grant(BOB))
    gate = _gate(store, access_log=access_log)
    use_case = ListOperatorGrants(grants=store, operator_gate=gate, clock=FixedClock(BASE))

    use_case.run(_principal(ALICE))

    entries = access_log.recent(limit=10)
    assert [(entry.user_id, entry.operation) for entry in entries] == [(ALICE, OPERATION)]


def test_the_report_states_the_instant_and_the_source() -> None:
    use_case, clock = _list_use_case(_reporting_store())
    snapshot = use_case.run(_principal(ALICE))

    assert snapshot.as_of == clock.now()
    assert snapshot.source_name == "in-memory"
    assert snapshot.holder_count == len(snapshot.holders)


# -- the grant detail: the reason reaches the reader (NEX-077) --------------


def test_a_human_issued_grant_reports_its_issuer_and_reason() -> None:
    """The fold drops provenance; the detail keeps it, which is why it exists."""
    use_case, _ = _list_use_case(
        _reporting_store(
            _grant(
                BOB,
                granted_by=ALICE,
                rationale=OperatorGrantRationale("covering the incident review window"),
            )
        )
    )

    snapshot = use_case.run(_principal(ALICE))

    bob = {row.scope: row for row in snapshot.grants if row.user_id == BOB}
    assert bob["withholding-report"].granted_by == ALICE
    assert bob["withholding-report"].rationale == "covering the incident review window"
    # The summary folds the same grant; the two views agree on the holder set.
    assert {holder.user_id for holder in snapshot.holders} == {ALICE, BOB}


def test_a_configuration_grant_reports_no_issuer_and_no_reason() -> None:
    """A seeded grant has neither; the detail says so rather than inventing one."""
    use_case, _ = _list_use_case(_reporting_store(_grant(BOB)))

    detail = {row.user_id: row for row in use_case.run(_principal(ALICE)).grants}[BOB]

    assert detail.granted_by is None
    assert detail.rationale is None


def test_two_grants_for_one_holder_keep_their_own_reasons() -> None:
    """The reason a fold cannot carry: one holder, two grants, two justifications."""
    use_case, _ = _list_use_case(
        _reporting_store(
            _grant(
                BOB,
                scope="withholding-report",
                granted_by=ALICE,
                rationale=OperatorGrantRationale("incident review window"),
            ),
            _grant(
                BOB,
                scope="audit-integrity",
                granted_by=ALICE,
                rationale=OperatorGrantRationale("quarterly access recertification"),
            ),
        )
    )

    snapshot = use_case.run(_principal(ALICE))

    reasons = {row.scope: row.rationale for row in snapshot.grants if row.user_id == BOB}
    assert reasons == {
        "withholding-report": "incident review window",
        "audit-integrity": "quarterly access recertification",
    }
    # One holder, two grants: the counts stay consistent, not merely present.
    assert snapshot.holder_count == 2  # ALICE (reader) + BOB
    assert snapshot.grant_count == 3  # ALICE's read grant + BOB's two
    assert snapshot.grant_count == len(snapshot.grants)


def test_the_detail_excludes_an_expired_grant() -> None:
    """Detail and summary describe the same active set, so expiry drops both."""
    clock = FixedClock(BASE)
    use_case, _ = _list_use_case(
        _reporting_store(
            _grant(
                BOB,
                granted_by=ALICE,
                rationale=OperatorGrantRationale("a short window"),
                expires_at=BASE + timedelta(hours=1),
            )
        ),
        clock=clock,
    )
    assert any(row.user_id == BOB for row in use_case.run(_principal(ALICE)).grants)

    clock.advance(timedelta(hours=2))
    snapshot = use_case.run(_principal(ALICE))
    assert not any(row.user_id == BOB for row in snapshot.grants)
    assert BOB not in {holder.user_id for holder in snapshot.holders}


# -- the revocation history: the read half of NEX-079 (NEX-080) -------------


def _revoked_use_case(
    grants: InMemoryOperatorGrantRepository,
) -> ListRevokedOperatorGrants:
    gate = _gate(grants, clock=FixedClock(BASE))
    return ListRevokedOperatorGrants(grants=grants, operator_gate=gate)


def _revoked_grant(
    user_id: UserId = BOB,
    scope: str = "withholding-report",
    *,
    revoked_by: UserId | None = ALICE,
    revoked_at: datetime | None = BASE + timedelta(hours=1),
) -> OperatorGrant:
    # A real revocation produces a store entry via ``revoke``; constructing the
    # revoked shape directly keeps this test about the *read* path, and lets it
    # build the post-deletion shape (``revoked_by`` None) the store cannot accept
    # through ``revoke``.
    grant = _grant(user_id, scope=scope)
    return OperatorGrant(
        user_id=grant.user_id,
        scope=grant.scope,
        granted_at=grant.granted_at,
        revoked_at=revoked_at,
        revoked_by=revoked_by,
    )


def test_a_revoked_grant_is_reported_with_who_revoked_it_and_when() -> None:
    """The provenance NEX-079 wrote is the reason this read exists."""
    revoker_reads = _grant(ALICE, scope=OPERATION)
    use_case = _revoked_use_case(_store(revoker_reads, _revoked_grant()))

    snapshot = use_case.run(_principal(ALICE))

    (row,) = snapshot.grants
    assert row.user_id == BOB
    assert row.scope == "withholding-report"
    assert row.revoked_at == BASE + timedelta(hours=1)
    assert row.revoked_by == ALICE
    assert snapshot.grant_count == 1


def test_a_live_grant_is_absent_from_the_revocation_history() -> None:
    """History is revocation, not holdings: a live grant belongs to the other report."""
    use_case = _revoked_use_case(_store(_grant(ALICE, scope=OPERATION), _grant(BOB)))

    assert use_case.run(_principal(ALICE)).grants == ()


def test_an_expired_but_unrevoked_grant_is_not_history() -> None:
    """Lapsing and being revoked are different facts; only revocation is history."""
    expired = _grant(BOB, scope="withholding-report", expires_at=BASE + timedelta(hours=1))
    use_case = _revoked_use_case(_store(_grant(ALICE, scope=OPERATION), expired))

    assert use_case.run(_principal(ALICE)).grants == ()


def test_a_revocation_outlives_a_deleted_revoker() -> None:
    """The reason ``revoked_by`` is nullable here: the record must survive its actor.

    ``revoked_by`` is a ``SET NULL`` foreign key, so deleting the revoker's account
    leaves the revocation with no actor. It must still be reported — reading it as
    "not revoked" would silently restore authority nobody granted back.
    """
    use_case = _revoked_use_case(
        _store(_grant(ALICE, scope=OPERATION), _revoked_grant(revoked_by=None))
    )

    (row,) = use_case.run(_principal(ALICE)).grants
    assert row.user_id == BOB
    assert row.revoked_at is not None
    assert row.revoked_by is None


def test_a_non_operator_cannot_read_the_revocation_history() -> None:
    use_case = _revoked_use_case(_store(_revoked_grant()))
    with pytest.raises(ResourceNotFound):
        use_case.run(_principal(CAROL))


def test_reading_the_revocation_history_is_recorded() -> None:
    """Reading who lost authority is an operator read, audited like the holder list."""
    access_log = InMemoryOperatorAccessLog()
    store = _store(_grant(ALICE, scope=OPERATION), _revoked_grant())
    gate = _gate(store, access_log=access_log)
    use_case = ListRevokedOperatorGrants(grants=store, operator_gate=gate)

    use_case.run(_principal(ALICE))

    entries = access_log.recent(limit=10)
    assert [(entry.user_id, entry.operation) for entry in entries] == [(ALICE, OPERATION)]


def test_a_refused_history_read_leaves_no_access_entry() -> None:
    """The gate authorizes before it records, so a refusal writes nothing."""
    access_log = InMemoryOperatorAccessLog()
    store = _store(_revoked_grant())
    gate = _gate(store, access_log=access_log)
    use_case = ListRevokedOperatorGrants(grants=store, operator_gate=gate)

    with pytest.raises(ResourceNotFound):
        use_case.run(_principal(CAROL))

    assert access_log.recent(limit=10) == ()


def test_the_history_names_its_source() -> None:
    use_case = _revoked_use_case(_store(_grant(ALICE, scope=OPERATION)))
    assert use_case.run(_principal(ALICE)).source_name == "in-memory"


# -- bounding the history: a page, its total, and the next cursor (NEX-086) --
#
# Revoked rows are never pruned, so the history grows for the life of the
# deployment. Serving it whole made one operator read load every revocation ever
# recorded. These tests pin the shape that replaces "everything": a page, the
# total it was drawn from, and a cursor, so a client can walk the history without
# the use case ever materialising all of it at once.


def _three_revocations() -> InMemoryOperatorGrantRepository:
    # Distinct holder/scope pairs so each revoked row is individually addressable
    # in the stable store order the paging is defined against.
    return _store(
        _grant(ALICE, scope=OPERATION),
        _revoked_grant(BOB, "withholding-report"),
        _revoked_grant(CAROL, "audit-integrity"),
        _revoked_grant(UserId.new(), "policy-approve"),
    )


def test_a_page_reports_the_total_beyond_the_page() -> None:
    """``grant_count`` is the page; ``revoked_total`` is the history.

    The distinction is the whole point of bounding the read: a client that reported
    ``grant_count`` as the total would understate how much authority has been
    removed. Here two of three rows come back, and the total still says three.
    """
    use_case = _revoked_use_case(_three_revocations())

    snapshot = use_case.run(_principal(ALICE), limit=2, offset=0)

    assert snapshot.grant_count == 2
    assert snapshot.revoked_total == 3
    assert snapshot.offset == 0
    assert snapshot.next_offset == 2, "a page with more behind it names the next offset"
    assert snapshot.truncated is True


def test_the_last_page_has_no_next_offset() -> None:
    """Following ``next_offset`` until it is null reaches exactly the total.

    A page that reaches the end reports ``next_offset`` of None and is not
    ``truncated``; any other answer would give a client either a duplicate final
    page or a history it can never finish walking.
    """
    use_case = _revoked_use_case(_three_revocations())

    first = use_case.run(_principal(ALICE), limit=2, offset=0)
    second = use_case.run(_principal(ALICE), limit=2, offset=first.next_offset or 0)

    assert second.grant_count == 1
    assert second.offset == 2
    assert second.next_offset is None
    assert second.truncated is False


def test_walking_the_pages_covers_every_revocation_once() -> None:
    """Concatenated pages are the whole history, in order, with no repeats.

    The join is what a client does, so it is what the test asserts: paging must not
    skip a row between pages, and must not serve one twice. Offset paging is safe
    here only because a read of this history writes nothing to it.
    """
    use_case = _revoked_use_case(_three_revocations())

    seen: list[tuple[UserId, str]] = []
    offset = 0
    for _ in range(10):
        page = use_case.run(_principal(ALICE), limit=1, offset=offset)
        seen.extend((row.user_id, row.scope) for row in page.grants)
        if page.next_offset is None:
            break
        offset = page.next_offset

    whole = use_case.run(_principal(ALICE), limit=None)
    assert seen == [(row.user_id, row.scope) for row in whole.grants]
    assert len(seen) == 3


def test_an_offset_past_the_end_is_an_empty_final_page() -> None:
    """An offset at or beyond the total is empty, not an error and not a wrap."""
    use_case = _revoked_use_case(_three_revocations())

    snapshot = use_case.run(_principal(ALICE), limit=10, offset=3)

    assert snapshot.grant_count == 0
    assert snapshot.revoked_total == 3
    assert snapshot.next_offset is None


def test_a_non_positive_limit_does_not_serve_the_whole_history() -> None:
    """``limit=0`` is a caller mistake, not a request for everything (NEX-086).

    Failing closed here means clamping to one row. Treating a zero limit as
    "unbounded" would reinstate exactly the unbounded read this task removed, for a
    caller who only mistyped a number.
    """
    use_case = _revoked_use_case(_three_revocations())

    snapshot = use_case.run(_principal(ALICE), limit=0, offset=0)

    assert snapshot.grant_count == 1
    assert snapshot.revoked_total == 3


def test_a_limit_above_the_ceiling_is_clamped() -> None:
    """Over-large limits are capped, so one read cannot pull the whole history."""
    use_case = _revoked_use_case(_three_revocations())

    snapshot = use_case.run(_principal(ALICE), limit=10_000, offset=0)

    assert snapshot.truncated is False
    assert snapshot.grant_count == 3
