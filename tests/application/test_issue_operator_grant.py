"""Issuing a bounded operator grant is itself a gated, attenuating operation (NEX-074).

``OperatorGrant`` has carried ``expires_at`` and ``granted_by`` since NEX-045 and
the store has been durable since NEX-046, but nothing in the running platform could
*write* a human-issued grant: the only writer was the configuration seed path,
which issues unbounded grants with ``granted_by=None``. So the bounded,
attributable grant the record exists for was unreachable. ``IssueOperatorGrant`` is
that missing caller, and these tests pin the four refusals that make it safe to
expose:

* **the issuer must be an operator** for ``operator-grant-issue`` — a caller who
  merely holds some other scope is refused exactly like a stranger (``404``);
* **no self-grant** — an operator cannot widen their own authority;
* **attenuation** — a scope the issuer does not hold is refused, so a grant can
  narrow authority across people but never amplify it;
* **bounded and known** — an unknown scope, or a deadline that
  is not in the future is a request error, not a stored no-op.

The clock is injected, so "now" and the deadline are values these tests choose.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from notes_service.application.errors import Conflict, ResourceNotFound
from notes_service.application.ports.clock import Clock
from notes_service.application.use_cases.operator_gate import WILDCARD, OperatorGate
from notes_service.application.use_cases.operator_grants import (
    ISSUE_OPERATION,
    IssueOperatorGrant,
    IssueOperatorGrantRequest,
)
from notes_service.domain.errors import ValidationError
from notes_service.domain.identity.operator_grant import OperatorGrant, OperatorGrantRationale
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
GHOST = UserId.parse("99999999-9999-9999-9999-999999999999")

BASE = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
DEADLINE = BASE + timedelta(hours=4)

RATIONALE = OperatorGrantRationale("covering Bob for the on-call rotation")


class FixedClock(Clock):
    def __init__(self, now: datetime) -> None:
        self.now_value = now

    def now(self) -> datetime:
        return self.now_value


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


def _gate(grants: InMemoryOperatorGrantRepository) -> OperatorGate:
    return OperatorGate(
        grants=grants,
        access_log=InMemoryOperatorAccessLog(),
        clock=FixedClock(BASE),
    )


def _use_case(
    grants: InMemoryOperatorGrantRepository,
    *,
    known_users: frozenset[UserId] = frozenset({ALICE, BOB, CAROL}),
) -> IssueOperatorGrant:
    return IssueOperatorGrant(
        grants=grants,
        operator_gate=_gate(grants),
        clock=FixedClock(BASE),
        user_exists=lambda user_id: user_id in known_users,
    )


def _issuer_grant(
    user_id: UserId = ALICE,
    scope: str = ISSUE_OPERATION,
    *,
    expires_at: datetime | None = None,
    granted_at: datetime = BASE,
) -> OperatorGrant:
    return OperatorGrant(user_id=user_id, scope=scope, granted_at=granted_at, expires_at=expires_at)


def _request(
    *,
    user_id: UserId = BOB,
    scope: str = "withholding-report",
    expires_at: datetime = DEADLINE,
    rationale: OperatorGrantRationale = RATIONALE,
) -> IssueOperatorGrantRequest:
    return IssueOperatorGrantRequest(
        user_id=user_id, scope=scope, expires_at=expires_at, rationale=rationale
    )


def test_an_operator_issues_a_bounded_grant_attributed_to_themselves() -> None:
    grants = _store(
        _issuer_grant(scope=ISSUE_OPERATION),
        # Attenuation: ALICE may only hand on a scope she already holds.
        _issuer_grant(scope="withholding-report"),
    )
    grant = _use_case(grants).execute(_principal(ALICE), _request())

    assert grant.user_id == BOB
    assert grant.scope == "withholding-report"
    # The issuer is the caller, never a request field.
    assert grant.granted_by == ALICE
    assert grant.granted_at == BASE
    assert grant.expires_at == DEADLINE

    # It really landed in the store and now admits Bob at the gate's clock.
    assert grants.active_for(BOB, now=BASE) == frozenset({"withholding-report"})


def test_a_caller_who_is_not_an_issuer_is_refused_like_a_stranger() -> None:
    """BOB holds a read scope but not the issue scope: refused as not-found."""
    grants = _store(_issuer_grant(ALICE, scope="withholding-report"))
    with pytest.raises(ResourceNotFound):
        _use_case(grants).execute(_principal(ALICE), _request(user_id=BOB))

    # A refusal must leave nothing behind.
    assert grants.active_for(BOB, now=BASE) == frozenset()


def test_an_operator_cannot_issue_a_grant_to_themselves() -> None:
    grants = _store(_issuer_grant())
    with pytest.raises(Conflict):
        _use_case(grants).execute(_principal(ALICE), _request(user_id=ALICE, scope=ISSUE_OPERATION))
    assert grants.active_for(ALICE, now=BASE) == frozenset({ISSUE_OPERATION})


def test_an_operator_cannot_issue_a_scope_they_do_not_hold() -> None:
    """Attenuation: an issuer narrows authority across people, never amplifies it."""
    grants = _store(_issuer_grant(ALICE, scope="withholding-report"))
    # ALICE may issue (she holds ISSUE_OPERATION only if listed) — give her both,
    # but not ``policy-publish``, which she then tries to mint.
    grants.add(_issuer_grant(ALICE, scope=ISSUE_OPERATION))

    with pytest.raises(Conflict):
        _use_case(grants).execute(_principal(ALICE), _request(user_id=BOB, scope="policy-publish"))
    assert grants.active_for(BOB, now=BASE) == frozenset()


def test_a_wildcard_issuer_may_issue_any_known_scope() -> None:
    grants = _store(_issuer_grant(ALICE, scope="*"))
    grant = _use_case(grants).execute(
        _principal(ALICE), _request(user_id=BOB, scope="policy-publish")
    )
    assert grant.scope == "policy-publish"


def test_a_lapsed_issuer_scope_cannot_be_handed_onward() -> None:
    """The attenuation check reads live scopes, not a remembered allowlist."""
    grants = _store(
        _issuer_grant(
            scope=ISSUE_OPERATION,
            granted_at=BASE - timedelta(hours=2),
            expires_at=BASE - timedelta(minutes=1),
        ),
        _issuer_grant(
            scope="withholding-report",
            granted_at=BASE - timedelta(hours=2),
            expires_at=BASE - timedelta(minutes=1),
        ),
    )
    # The issue scope itself has lapsed, so the gate refuses before attenuation.
    with pytest.raises(ResourceNotFound):
        _use_case(grants).execute(
            _principal(ALICE), _request(user_id=BOB, scope="withholding-report")
        )


def test_an_unknown_scope_is_a_request_error() -> None:
    grants = _store(_issuer_grant(ALICE, scope="*"))
    with pytest.raises(ValidationError):
        _use_case(grants).execute(
            _principal(ALICE), _request(user_id=BOB, scope="not-a-real-scope")
        )


def test_a_grant_to_an_unknown_user_is_a_request_error() -> None:
    grants = _store(_issuer_grant(ALICE, scope="*"))
    with pytest.raises(ValidationError):
        _use_case(grants).execute(_principal(ALICE), _request(user_id=GHOST))


def test_a_deadline_in_the_past_is_a_request_error() -> None:
    grants = _store(_issuer_grant(ALICE, scope="*"))
    with pytest.raises(ValidationError):
        _use_case(grants).execute(_principal(ALICE), _request(user_id=BOB, expires_at=BASE))


def test_the_stored_grant_carries_the_issuer_the_deadline_and_the_reason() -> None:
    """The audit trail of a widening is the row itself, so pin what it holds.

    NEX-076 closed the gap this test used to document: there *was* no rationale
    column, so a grant could record *who* widened *what* until *when* but never
    *why*. Now the reason is a required field and it is stored on the grant, so
    this asserts all three facts an auditor reads back.
    """
    grants = _store(_issuer_grant(ALICE, scope="*"))
    grant = _use_case(grants).execute(_principal(ALICE), _request(user_id=BOB))

    assert grant.granted_by == ALICE
    assert grant.expires_at == DEADLINE
    assert grant.granted_at == BASE
    assert grant.rationale == RATIONALE
    assert grant.is_active(now=BASE)


def test_the_issued_grant_is_readable_back_from_the_store() -> None:
    """The reason survives the round trip through the store, not just the object.

    A rationale that existed only on the returned value would be a field
    validated and discarded — the exact defect this slice fixes. Reading the
    store back is what proves it was persisted.
    """
    grants = _store(_issuer_grant(ALICE, scope="*"))
    _use_case(grants).execute(_principal(ALICE), _request(user_id=BOB))

    stored = grants.active_for(BOB, now=BASE)
    assert stored == frozenset({"withholding-report"})
    (holder,) = [g for g in grants.active_grants(now=BASE) if g.user_id == BOB]
    assert holder.rationale == RATIONALE
    assert holder.granted_by == ALICE


def test_a_grant_may_not_outlive_the_issuers_own_grant_of_that_scope() -> None:
    """Temporal attenuation: a grant narrows authority, it never extends it (NEX-084).

    Scope attenuation already stops an issuer handing on a permission they lack.
    It says nothing about *lifetime*, so an operator whose own grant lapses in an
    hour could issue the same scope to someone else for a month — widening
    authority in the only dimension scope attenuation does not cover. An incident
    grant is meant to cover the incident; if it can be converted into a longer one
    by the person it was handed to, "bounded" means nothing.
    """
    grants = _store(
        _issuer_grant(scope=ISSUE_OPERATION),
        _issuer_grant(
            scope="withholding-report",
            granted_at=BASE - timedelta(hours=3),
            expires_at=BASE + timedelta(hours=1),
        ),
    )
    # ALICE's own withholding-report lapses at BASE+1h; she asks for BASE+4h.
    with pytest.raises(Conflict):
        _use_case(grants).execute(_principal(ALICE), _request(user_id=BOB, expires_at=DEADLINE))
    assert grants.active_for(BOB, now=BASE) == frozenset()


def test_a_grant_inside_the_issuers_window_is_allowed() -> None:
    """The rule is a ceiling, not a prohibition: a shorter window is attenuation.

    Pinned so the fix cannot be "refuse everything with a deadline". The point is
    that the issued grant must end no later than the authority it derives from.
    """
    grants = _store(
        _issuer_grant(scope=ISSUE_OPERATION),
        _issuer_grant(
            scope="withholding-report",
            granted_at=BASE - timedelta(hours=3),
            expires_at=BASE + timedelta(hours=3),
        ),
    )
    grant = _use_case(grants).execute(
        _principal(ALICE), _request(user_id=BOB, expires_at=BASE + timedelta(hours=2))
    )
    assert grant.expires_at == BASE + timedelta(hours=2)


def test_a_grant_at_exactly_the_issuers_deadline_is_allowed() -> None:
    """Equal to the ceiling is inside it — the child ends when the parent does."""
    ceiling = BASE + timedelta(hours=1)
    grants = _store(
        _issuer_grant(scope=ISSUE_OPERATION),
        _issuer_grant(
            scope="withholding-report",
            granted_at=BASE - timedelta(hours=3),
            expires_at=ceiling,
        ),
    )
    grant = _use_case(grants).execute(_principal(ALICE), _request(user_id=BOB, expires_at=ceiling))
    assert grant.expires_at == ceiling


def test_the_ceiling_is_the_latest_live_grant_not_the_earliest() -> None:
    """An issuer holding several grants of one scope may use the strongest one.

    ``withholding-report`` can be held by more than one grant (a short incident
    grant alongside a longer one). The ceiling is the furthest they are
    authorized *as of now*, not the nearest deadline — narrowing to the nearest
    would refuse a legitimate hand-out to an operator whose longer grant plainly
    covers it.
    """
    grants = _store(
        _issuer_grant(scope=ISSUE_OPERATION),
        _issuer_grant(
            scope="withholding-report",
            granted_at=BASE - timedelta(hours=3),
            expires_at=BASE + timedelta(minutes=5),
        ),
        _issuer_grant(
            scope="withholding-report",
            granted_at=BASE - timedelta(hours=3),
            expires_at=BASE + timedelta(hours=6),
        ),
    )
    grant = _use_case(grants).execute(
        _principal(ALICE), _request(user_id=BOB, expires_at=BASE + timedelta(hours=5))
    )
    assert grant.expires_at == BASE + timedelta(hours=5)


def test_an_unbounded_grant_may_issue_up_to_the_live_ceiling() -> None:
    """A configuration grant never lapses, so it is the ceiling and imposes no new one.

    The seed path issues unbounded grants (``granted_by=None``). A holder of one
    must remain able to hand the scope on with any deadline — the fix must not
    silently make the platform's own operators unable to issue at all.
    """
    grants = _store(
        _issuer_grant(scope=ISSUE_OPERATION),
        _issuer_grant(scope="withholding-report", expires_at=None),
    )
    grant = _use_case(grants).execute(
        _principal(ALICE), _request(user_id=BOB, expires_at=BASE + timedelta(days=30))
    )
    assert grant.expires_at == BASE + timedelta(days=30)


def test_a_wildcard_issuer_is_still_bounded_by_their_own_wildcard_deadline() -> None:
    """The wildcard covers every scope; it does not cover every *time*.

    A wildcard means "all operations", so the scope check passes. Its expiry is
    still a bound on the authority it represents, so the temporal ceiling applies
    to a wildcard holder exactly as it does to a single-scope one.
    """
    grants = _store(
        _issuer_grant(
            scope=WILDCARD,
            granted_at=BASE - timedelta(hours=3),
            expires_at=BASE + timedelta(hours=1),
        )
    )
    with pytest.raises(Conflict):
        _use_case(grants).execute(
            _principal(ALICE), _request(user_id=BOB, scope="policy-publish", expires_at=DEADLINE)
        )


def test_a_wildcard_issuer_with_the_latest_grant_may_issue_any_scope_within_it() -> None:
    """The wildcard ceiling is the latest of the wildcard grants, not the nearest."""
    grants = _store(
        _issuer_grant(
            scope=WILDCARD,
            granted_at=BASE - timedelta(hours=3),
            expires_at=BASE + timedelta(hours=1),
        ),
        _issuer_grant(
            scope=WILDCARD,
            granted_at=BASE - timedelta(hours=3),
            expires_at=BASE + timedelta(hours=8),
        ),
    )
    grant = _use_case(grants).execute(
        _principal(ALICE), _request(user_id=BOB, scope="policy-publish", expires_at=DEADLINE)
    )
    assert grant.expires_at == DEADLINE


def test_the_ceiling_applies_only_to_live_grants_of_the_named_scope() -> None:
    """A lapsed longer grant is not a ceiling: it confers nothing to hand on.

    The ceiling is computed from *active* grants only. Counting a lapsed one would
    let an operator whose authority has already ended still mint a window, which
    inverts the rule.
    """
    grants = _store(
        _issuer_grant(scope=ISSUE_OPERATION),
        _issuer_grant(
            scope="withholding-report",
            granted_at=BASE - timedelta(hours=9),
            expires_at=BASE - timedelta(hours=3),
        ),
    )
    # Only a lapsed grant of the scope exists, so it is no ceiling at all: the
    # issuer does not hold the scope and scope attenuation refuses first.
    with pytest.raises(Conflict):
        _use_case(grants).execute(
            _principal(ALICE), _request(user_id=BOB, expires_at=BASE + timedelta(hours=2))
        )
