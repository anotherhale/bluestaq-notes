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
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.application.use_cases.operator_grants import (
    ISSUE_OPERATION,
    IssueOperatorGrant,
    IssueOperatorGrantRequest,
)
from notes_service.domain.errors import ValidationError
from notes_service.domain.identity.operator_grant import OperatorGrant
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
) -> IssueOperatorGrantRequest:
    return IssueOperatorGrantRequest(user_id=user_id, scope=scope, expires_at=expires_at)


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


def test_the_stored_grant_carries_the_issuer_and_the_deadline() -> None:
    """The audit trail of a widening is the row itself, so pin what it holds.

    There is no ``rationale`` column, so nothing may promise one: this asserts the
    two facts that *are* recorded — who issued the grant and when it lapses.
    """
    grants = _store(_issuer_grant(ALICE, scope="*"))
    grant = _use_case(grants).execute(_principal(ALICE), _request(user_id=BOB))

    assert grant.granted_by == ALICE
    assert grant.expires_at == DEADLINE
    assert grant.granted_at == BASE
    assert grant.is_active(now=BASE)
