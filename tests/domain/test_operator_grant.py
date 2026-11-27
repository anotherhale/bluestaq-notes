"""A stored operator grant: scope, lifetime, and provenance (NEX-045).

The static allowlist (``OPS_OPERATOR_USER_IDS``) could not expire and could not
name its holders. These tests pin the two properties that make the stored grant
worth having, both of which are *domain* rules and are therefore testable without
a settings object, a clock, or a store:

* **expiry is fail-closed at the boundary.** A grant is dead at the instant it
  expires (``now >= expires_at``), not one tick later, so a deadline is a
  deadline and not an approximation of one.
* **a grant is not valid before it exists.** ``not_before`` in the future, or a
  clock that puts ``granted_at`` ahead of ``now``, confers nothing — a grant
  cannot be used in a window it has not reached.

The constructor rejects the mistakes that would otherwise look like working
grants until the first check: an empty scope, a deadline at or before issue, and
a validity window that is empty because ``not_before`` is past ``expires_at``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from notes_service.domain.errors import ValidationError
from notes_service.domain.identity.operator_grant import MAX_SCOPE_LENGTH, OperatorGrant
from notes_service.domain.identity.value_objects import UserId

ALICE = UserId.parse("11111111-1111-1111-1111-111111111111")
BOB = UserId.parse("22222222-2222-2222-2222-222222222222")

GRANTED = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)


def _grant(**overrides: object) -> OperatorGrant:
    fields: dict[str, object] = {
        "user_id": ALICE,
        "scope": "withholding-report",
        "granted_at": GRANTED,
    }
    fields.update(overrides)
    return OperatorGrant(**fields)  # type: ignore[arg-type]


# -- lifetime ---------------------------------------------------------------


def test_an_unbounded_grant_is_active_once_issued() -> None:
    grant = _grant()
    assert grant.is_active(now=GRANTED)
    assert grant.is_active(now=GRANTED + timedelta(days=3650))
    assert not grant.is_expiring


def test_a_grant_is_dead_at_the_instant_it_expires() -> None:
    """``now >= expires_at``: the boundary itself is dead, not one tick later."""
    expires = GRANTED + timedelta(hours=1)
    grant = _grant(expires_at=expires)

    assert grant.is_active(now=expires - timedelta(seconds=1))
    assert not grant.is_active(now=expires)
    assert not grant.is_active(now=expires + timedelta(seconds=1))
    assert grant.is_expiring


def test_a_grant_is_not_active_before_it_was_issued() -> None:
    """Clock skew that put ``granted_at`` ahead of ``now`` must not authorise."""
    assert not _grant().is_active(now=GRANTED - timedelta(seconds=1))


def test_a_grant_is_not_active_before_its_not_before() -> None:
    not_before = GRANTED + timedelta(hours=1)
    grant = _grant(not_before=not_before)

    assert not grant.is_active(now=not_before - timedelta(seconds=1))
    assert grant.is_active(now=not_before)


def test_a_grant_inside_its_window_is_active() -> None:
    grant = _grant(
        not_before=GRANTED + timedelta(minutes=5),
        expires_at=GRANTED + timedelta(hours=1),
    )
    assert grant.is_active(now=GRANTED + timedelta(minutes=30))


# -- construction rejects the mistakes -------------------------------------


@pytest.mark.parametrize("scope", ["", "   ", "\t"])
def test_an_empty_scope_confers_nothing_and_is_rejected(scope: str) -> None:
    with pytest.raises(ValidationError):
        _grant(scope=scope)


def test_a_scope_with_surrounding_whitespace_is_rejected() -> None:
    """Rejected rather than trimmed: ``" a"`` and ``"a"`` must not both exist."""
    with pytest.raises(ValidationError):
        _grant(scope=" withholding-report")


def test_an_overlong_scope_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _grant(scope="x" * (MAX_SCOPE_LENGTH + 1))


def test_a_deadline_at_or_before_issue_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _grant(expires_at=GRANTED)
    with pytest.raises(ValidationError):
        _grant(expires_at=GRANTED - timedelta(seconds=1))


def test_an_empty_validity_window_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _grant(
            not_before=GRANTED + timedelta(hours=2),
            expires_at=GRANTED + timedelta(hours=1),
        )


def test_the_user_must_be_a_user_id() -> None:
    with pytest.raises(ValidationError):
        _grant(user_id=str(ALICE))


def test_the_granter_must_be_a_user_id_or_none() -> None:
    assert _grant(granted_by=None).granted_by is None
    assert _grant(granted_by=BOB).granted_by == BOB
    with pytest.raises(ValidationError):
        _grant(granted_by=str(BOB))


# -- provenance -------------------------------------------------------------


def test_a_seeded_grant_has_no_human_granter() -> None:
    """``granted_by=None`` is an honest "config issued this", not a gap."""
    assert _grant().granted_by is None


def test_a_grant_records_who_issued_it() -> None:
    assert _grant(granted_by=BOB).granted_by == BOB


def test_grants_compare_by_value() -> None:
    assert _grant() == _grant()
    assert _grant() != _grant(scope="audit-integrity")
