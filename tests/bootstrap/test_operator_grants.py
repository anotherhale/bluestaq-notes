"""``ops_operator_user_ids`` is parsed and validated at startup (NEX-044).

The gate matches ``UserId`` values and exact operation names. A typo in either
half would match nobody, and the endpoint would look broken for the one operator
it was meant to serve — so the value is rejected at startup, where the error
names the offending entry, instead of at request time, where it would surface as
an unexplained 404.

Two shapes are rejected rather than guessed at:

* a bare id with no scope. Reading it as "all operations" would silently widen
  a legacy entry into a wildcard grant — the escalation this field prevents.
* an empty scope, which names nothing.

The scope map is a *property* over the validated entries rather than a field, so
there is no second copy of the allowlist that could disagree with the first.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from notes_service.bootstrap.config import OPERATOR_WILDCARD, Settings
from notes_service.domain.identity.value_objects import UserId

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


def _settings(*entries: str) -> Settings:
    return Settings(ops_operator_user_ids=list(entries))


def test_an_unset_allowlist_grants_nothing() -> None:
    assert Settings().operator_scopes == {}


def test_a_grant_is_parsed_into_its_scope() -> None:
    scopes = _settings(f"{ALICE}:withholding-report").operator_scopes
    assert scopes == {UserId.parse(ALICE): frozenset({"withholding-report"})}


def test_a_bare_id_is_rejected_rather_than_read_as_a_wildcard() -> None:
    with pytest.raises(ValidationError, match="must be '<user-id>:<scope>'"):
        _settings(ALICE)


def test_an_empty_scope_is_rejected() -> None:
    with pytest.raises(ValidationError, match="names no scope"):
        _settings(f"{ALICE}:")


def test_a_non_uuid_id_is_rejected() -> None:
    with pytest.raises(ValidationError, match="non-UUID user id"):
        _settings("not-a-uuid:withholding-report")


def test_entries_for_one_operator_union_their_scopes() -> None:
    """Two grants for one person accumulate; they do not overwrite each other."""
    scopes = _settings(
        f"{ALICE}:withholding-report",
        f"{ALICE}:audit-integrity",
    ).operator_scopes
    assert scopes == {UserId.parse(ALICE): frozenset({"withholding-report", "audit-integrity"})}


def test_several_operators_keep_their_own_scopes() -> None:
    scopes = _settings(
        f"{ALICE}:withholding-report",
        f"{BOB}:{OPERATOR_WILDCARD}",
    ).operator_scopes
    assert scopes[UserId.parse(ALICE)] == frozenset({"withholding-report"})
    assert scopes[UserId.parse(BOB)] == frozenset({OPERATOR_WILDCARD})


def test_the_comma_separated_environment_form_is_accepted() -> None:
    """``.env`` carries one comma-separated string, not a Python list."""
    settings = Settings(ops_operator_user_ids=f"{ALICE}:withholding-report,{BOB}:audit-integrity")
    assert set(settings.operator_scopes) == {UserId.parse(ALICE), UserId.parse(BOB)}


def test_whitespace_around_an_entry_is_tolerated() -> None:
    scopes = _settings(f"  {ALICE} : withholding-report  ").operator_scopes
    assert scopes == {UserId.parse(ALICE): frozenset({"withholding-report"})}
