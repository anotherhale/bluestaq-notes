"""Revoking an operator grant over HTTP (NEX-079).

The use case and the stores are covered where they live. This file covers what
they cannot: that the route, the request/response schemas and the container
wiring agree, and that a revocation made through the API really does remove the
holder's authority from the gate — the only claim that matters, because a
revocation that deleted a row without stopping the permission would be worse
than no revocation at all.

The refusal shapes mirror issue deliberately: ``404`` for a non-operator (never
``403``, so probing cannot confirm the surface exists), ``409`` for a self-revoke
and for revoking authority nobody holds, ``422`` for a malformed body.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, CAROL, auth_headers, operator_grant

GRANTS_PATH = "/api/v1/ops/operator-grants"
REVOKE_PATH = "/api/v1/ops/operator-grants/revoke"


def _client(app: FastAPI, **settings_updates: object) -> TestClient:
    settings = app.state.container.settings.model_copy(update=settings_updates)
    from notes_service.bootstrap import app as app_module

    return TestClient(app_module.create_app(settings))


@pytest.fixture
def revoker_client(app: FastAPI) -> TestClient:
    # ALICE may revoke. BOB holds the scope she will take away, but not the revoke
    # scope, so he is the subject of the action and not an actor in it.
    return _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-revoke"),
            operator_grant(ALICE, "operator-access-report"),
            operator_grant(BOB, "withholding-report"),
        ],
    )


def _revoke_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {"user_id": BOB, "scope": "withholding-report"}
    body.update(overrides)
    return body


def test_a_revoked_scope_disappears_from_the_holder_report(
    revoker_client: TestClient,
) -> None:
    """The end-to-end claim: revoke, then the holder view no longer lists it."""
    before = revoker_client.get(GRANTS_PATH, headers=auth_headers(ALICE))
    assert before.status_code == 200, before.text
    assert any(
        grant["user_id"] == BOB and grant["scope"] == "withholding-report"
        for grant in before.json()["grants"]
    )

    revoked = revoker_client.post(REVOKE_PATH, json=_revoke_body(), headers=auth_headers(ALICE))
    assert revoked.status_code == 200, revoked.text
    assert revoked.json() == {
        "user_id": BOB,
        "scope": "withholding-report",
        "grants_revoked": 1,
    }

    after = revoker_client.get(GRANTS_PATH, headers=auth_headers(ALICE))
    assert after.status_code == 200, after.text
    assert not any(
        grant["user_id"] == BOB and grant["scope"] == "withholding-report"
        for grant in after.json()["grants"]
    ), "a revoked grant must not be reported as live"


def test_a_revoked_operator_can_no_longer_use_the_scope(app: FastAPI) -> None:
    """Revocation must stop the permission, not merely hide a row.

    BOB's liveness is proven by an action that only a live grant allows: reading
    the withholding report. That succeeds before the revocation and is refused
    afterwards with the same ``404`` any outsider receives.
    """
    client = _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-revoke"),
            operator_grant(BOB, "withholding-report"),
        ],
    )
    withholding = "/api/v1/ops/withholding-report"
    assert client.get(withholding, headers=auth_headers(BOB)).status_code == 200

    revoked = client.post(REVOKE_PATH, json=_revoke_body(), headers=auth_headers(ALICE))
    assert revoked.status_code == 200, revoked.text

    assert client.get(withholding, headers=auth_headers(BOB)).status_code == 404


def test_revoking_twice_is_a_conflict_the_second_time(revoker_client: TestClient) -> None:
    """A revocation is not idempotent by design: the second call has nothing to do.

    Reporting ``200`` again would tell a responder their second attempt removed
    something the first already had — the same "don't claim what you didn't do"
    rule that makes the first no-op case a ``409``.
    """
    first = revoker_client.post(REVOKE_PATH, json=_revoke_body(), headers=auth_headers(ALICE))
    assert first.status_code == 200, first.text
    assert first.json()["grants_revoked"] == 1

    second = revoker_client.post(REVOKE_PATH, json=_revoke_body(), headers=auth_headers(ALICE))
    assert second.status_code == 409, second.text


def test_a_revoked_scope_can_be_issued_again(app: FastAPI) -> None:
    """Revocation ends one grant; it is not a permanent tombstone on the scope.

    A responder who revokes by mistake must be able to re-issue, and the new grant
    must be live. If revocation wrote a "never again" marker, recovery would need
    a deployment change — turning a normal operator action into an outage.
    """
    client = _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-revoke"),
            operator_grant(ALICE, "operator-grant-issue"),
            operator_grant(ALICE, "operator-access-report"),
            operator_grant(ALICE, "withholding-report"),
            operator_grant(BOB, "withholding-report"),
        ],
    )
    revoked = client.post(REVOKE_PATH, json=_revoke_body(), headers=auth_headers(ALICE))
    assert revoked.status_code == 200, revoked.text

    reissued = client.post(
        GRANTS_PATH,
        json={
            "user_id": BOB,
            "scope": "withholding-report",
            "expires_at": "2099-01-01T00:00:00Z",
            "rationale": "re-issued after the revocation was found to be a mistake",
        },
        headers=auth_headers(ALICE),
    )
    assert reissued.status_code == 201, reissued.text

    listed = client.get(GRANTS_PATH, headers=auth_headers(ALICE))
    assert listed.status_code == 200, listed.text
    assert any(
        grant["user_id"] == BOB and grant["scope"] == "withholding-report"
        for grant in listed.json()["grants"]
    ), "a re-issued grant must be live again"


def test_revoking_authority_nobody_holds_is_a_conflict(revoker_client: TestClient) -> None:
    """A responder must not be told authority was removed when it was not."""
    response = revoker_client.post(
        REVOKE_PATH,
        json=_revoke_body(scope="audit-integrity"),
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 409, response.text


def test_an_operator_cannot_revoke_their_own_grant(app: FastAPI) -> None:
    """Self-revoke is refused, the mirror of the self-grant rule."""
    client = _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-revoke"),
            operator_grant(ALICE, "withholding-report"),
        ],
    )
    response = client.post(
        REVOKE_PATH,
        json=_revoke_body(user_id=ALICE),
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 409, response.text


def test_a_non_operator_cannot_revoke(app: FastAPI) -> None:
    """CAROL is an ordinary user: ``404``, not ``403``."""
    client = _client(
        app,
        ops_operator_user_ids=[operator_grant(BOB, "withholding-report")],
    )
    response = client.post(REVOKE_PATH, json=_revoke_body(), headers=auth_headers(CAROL))
    assert response.status_code == 404, response.text


def test_a_holder_of_another_scope_cannot_revoke(app: FastAPI) -> None:
    """Holding *some* operator scope is not holding the revoke scope."""
    client = _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "withholding-report"),
            operator_grant(BOB, "withholding-report"),
        ],
    )
    response = client.post(REVOKE_PATH, json=_revoke_body(), headers=auth_headers(ALICE))
    assert response.status_code == 404, response.text


def test_an_unknown_scope_is_refused(app: FastAPI) -> None:
    client = _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-revoke"),
            operator_grant(BOB, "withholding-report"),
        ],
    )
    response = client.post(
        REVOKE_PATH,
        json=_revoke_body(scope="not-a-real-scope"),
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 422, response.text


def test_a_malformed_user_id_is_refused_at_the_boundary(revoker_client: TestClient) -> None:
    response = revoker_client.post(
        REVOKE_PATH,
        json=_revoke_body(user_id="not-a-uuid"),
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 422, response.text


def test_a_body_cannot_name_its_own_revoker(revoker_client: TestClient) -> None:
    """``revoked_by`` is not an accepted field; the issuer is the caller."""
    response = revoker_client.post(
        REVOKE_PATH,
        json=_revoke_body(revoked_by=CAROL),
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 422, response.text


def test_the_wildcard_grant_can_be_revoked(app: FastAPI) -> None:
    """The strongest grant in the system must be removable through the platform.

    ``"*"`` covers every operation, so an operator holding it is the most
    powerful actor there is. Until NEX-085 the revoke guard reused the *issue*
    allowlist, which deliberately excludes ``"*"``: revocation therefore refused
    the wildcard as an unknown scope, and a configuration-seeded wildcard
    operator could be stood down only by editing the deployment seed — the very
    "edit configuration to revoke" hole NEX-079 existed to close. Revocation
    removes authority, so the fail-closed direction is to permit it.
    """
    client = _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-revoke"),
            operator_grant(BOB, "*"),
        ],
    )
    withholding = "/api/v1/ops/withholding-report"
    assert client.get(withholding, headers=auth_headers(BOB)).status_code == 200

    revoked = client.post(REVOKE_PATH, json=_revoke_body(scope="*"), headers=auth_headers(ALICE))
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["grants_revoked"] == 1

    assert client.get(withholding, headers=auth_headers(BOB)).status_code == 404


def test_revoking_the_wildcard_leaves_an_explicit_scope_in_place(app: FastAPI) -> None:
    """Naming ``"*"`` is a specific revocation, not "revoke everything".

    Revocation is keyed by ``(user, scope)``, so removing the wildcard must leave
    any explicit grant beside it live. If it also removed those, ``"*"`` would be
    a blanket wipe wearing a scope's name, and the audit entry would understate
    what the responder actually took away.
    """
    client = _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-revoke"),
            operator_grant(ALICE, "operator-access-report"),
            operator_grant(BOB, "*"),
            operator_grant(BOB, "withholding-report"),
        ],
    )
    withholding = "/api/v1/ops/withholding-report"

    revoked = client.post(REVOKE_PATH, json=_revoke_body(scope="*"), headers=auth_headers(ALICE))
    assert revoked.status_code == 200, revoked.text

    # The explicit scope survives the wildcard's removal, so BOB still reads it.
    assert client.get(withholding, headers=auth_headers(BOB)).status_code == 200

    holders = client.get(GRANTS_PATH, headers=auth_headers(ALICE))
    assert holders.status_code == 200, holders.text
    bob = next(row for row in holders.json()["holders"] if row["user_id"] == BOB)
    assert bob["scopes"] == ["withholding-report"], "only the explicit scope must remain"


def test_an_unknown_scope_is_still_refused_now_that_the_wildcard_is_accepted(
    app: FastAPI,
) -> None:
    """Widening the guard to ``"*"`` must not widen it to everything.

    The wildcard was added to the revocable set precisely *because* it names a
    real, enforced grant. A token no use case checks still names nothing, so it
    stays a ``422`` — otherwise a typo would report a revocation that removed no
    authority, the exact "don't claim what you didn't do" failure a ``409`` for a
    no-op revocation already guards against.
    """
    client = _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-revoke"),
            operator_grant(BOB, "*"),
        ],
    )
    response = client.post(
        REVOKE_PATH,
        json=_revoke_body(scope="not-a-real-scope"),
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 422, response.text
