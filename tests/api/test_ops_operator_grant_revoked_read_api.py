"""Reading revoked operator grants over HTTP (NEX-080).

NEX-079 recorded a revocation as an attributable fact — ``revoked_at`` and
``revoked_by`` stamped onto the grant — and nothing read it back. A revocation
that cannot be read is a write with no audience: "who removed whose authority, and
when" stayed answerable only by opening the database by hand.

This file covers what the use case and the stores cannot: that the route, the
response schema and the container wiring agree, that the read is gated like the
holder report, and — the claim that matters — that a revocation made through the
API actually appears here with its provenance. A report that listed revocations
without naming the revoker would be indistinguishable from one that guessed.

The refusal shapes mirror the holder report deliberately: ``404`` for a
non-operator, never ``403``, so probing cannot confirm that the surface exists.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, CAROL, auth_headers, operator_grant

GRANTS_PATH = "/api/v1/ops/operator-grants"
REVOKE_PATH = "/api/v1/ops/operator-grants/revoke"
REVOKED_PATH = "/api/v1/ops/operator-grants/revoked"


def _client(app: FastAPI, **settings_updates: object) -> TestClient:
    settings = app.state.container.settings.model_copy(update=settings_updates)
    from notes_service.bootstrap import app as app_module

    return TestClient(app_module.create_app(settings))


@pytest.fixture
def revoker_client(app: FastAPI) -> TestClient:
    # ALICE may revoke and may read the report. BOB holds the scope she takes away.
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


def test_the_report_is_empty_before_anything_is_revoked(revoker_client: TestClient) -> None:
    """An empty history is an honest answer, not a missing one.

    ``grant_count`` of zero with an empty list is the correct shape for "nobody has
    had authority removed"; a client must not read it as the endpoint being broken.
    """
    response = revoker_client.get(REVOKED_PATH, headers=auth_headers(ALICE))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["grants"] == []
    assert body["grant_count"] == 0
    assert body["source_name"]


def test_a_revocation_appears_here_with_who_revoked_it(revoker_client: TestClient) -> None:
    """The end-to-end claim, and the reason the endpoint exists.

    Revoke through the API, then read the history: the grant is listed, its
    ``revoked_at`` is set, and ``revoked_by`` names the operator who did it. A row
    that appeared without the revoker would be the same unanswerable record NEX-079
    already left in the table.
    """
    revoked = revoker_client.post(REVOKE_PATH, json=_revoke_body(), headers=auth_headers(ALICE))
    assert revoked.status_code == 200, revoked.text

    response = revoker_client.get(REVOKED_PATH, headers=auth_headers(ALICE))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["grant_count"] == 1
    (grant,) = body["grants"]
    assert grant["user_id"] == BOB
    assert grant["scope"] == "withholding-report"
    assert grant["revoked_at"] is not None
    assert grant["revoked_by"] == str(ALICE)


def test_a_revoked_grant_leaves_the_live_report_but_stays_in_the_history(
    revoker_client: TestClient,
) -> None:
    """The two reports partition the store: live here, revoked there, never both.

    Before the revocation the grant is live and absent from history; afterwards it
    is the reverse. If it appeared in both, a reader could not tell whether a grant
    currently works.
    """
    before = revoker_client.get(REVOKED_PATH, headers=auth_headers(ALICE))
    assert before.status_code == 200, before.text
    assert before.json()["grant_count"] == 0

    revoked = revoker_client.post(REVOKE_PATH, json=_revoke_body(), headers=auth_headers(ALICE))
    assert revoked.status_code == 200, revoked.text

    live = revoker_client.get(GRANTS_PATH, headers=auth_headers(ALICE))
    assert live.status_code == 200, live.text
    assert not any(
        grant["user_id"] == BOB and grant["scope"] == "withholding-report"
        for grant in live.json()["grants"]
    ), "a revoked grant must not be reported as live"

    history = revoker_client.get(REVOKED_PATH, headers=auth_headers(ALICE))
    assert history.status_code == 200, history.text
    assert any(
        grant["user_id"] == BOB and grant["scope"] == "withholding-report"
        for grant in history.json()["grants"]
    ), "a revoked grant must be readable as revoked"


def test_a_non_operator_cannot_read_the_revocation_history(app: FastAPI) -> None:
    """CAROL is an ordinary user: ``404``, not ``403``.

    Reading who lost their authority is at least as sensitive as reading who holds
    it, so it carries the same non-disclosure rule: the surface must not confirm
    its own existence to an outsider.
    """
    client = _client(
        app,
        ops_operator_user_ids=[operator_grant(BOB, "withholding-report")],
    )
    response = client.get(REVOKED_PATH, headers=auth_headers(CAROL))
    assert response.status_code == 404, response.text


def test_a_holder_of_another_scope_cannot_read_the_history(app: FastAPI) -> None:
    """Holding *some* operator scope is not holding the report scope."""
    client = _client(
        app,
        ops_operator_user_ids=[operator_grant(ALICE, "withholding-report")],
    )
    response = client.get(REVOKED_PATH, headers=auth_headers(ALICE))
    assert response.status_code == 404, response.text


# -- the history is served a page at a time over HTTP (NEX-086) --------------


def _revoke_scope(client: TestClient, scope: str) -> None:
    revoked = client.post(REVOKE_PATH, json=_revoke_body(scope=scope), headers=auth_headers(ALICE))
    assert revoked.status_code == 200, revoked.text


def test_the_response_carries_the_paging_fields(revoker_client: TestClient) -> None:
    """The default page states its total, its offset, and its cursor.

    The fields exist whether or not the caller asked for paging, so a client that
    never passes ``limit``/``offset`` still learns ``revoked_total`` and whether it
    saw all of it. Servicing that with an implicit "everything" would reintroduce
    the unbounded read for every caller.
    """
    _revoke_scope(revoker_client, "withholding-report")

    response = revoker_client.get(REVOKED_PATH, headers=auth_headers(ALICE))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revoked_total"] == 1
    assert body["offset"] == 0
    assert body["next_offset"] is None
    assert body["truncated"] is False


def test_paging_reaches_every_revocation_and_the_total_holds(app: FastAPI) -> None:
    """Two revocations, one per page: the pages join to the whole set.

    The wire shape is what an auditor has to walk, so the walk is what the test
    does — first page names the second by ``next_offset``, the second ends the
    history, and ``revoked_total`` stays two throughout.
    """
    # Pinned here rather than borrowed from ``revoker_client``: that fixture gives
    # BOB one scope, and two revocations need two live grants to take away.
    client = _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-revoke"),
            operator_grant(ALICE, "operator-access-report"),
            operator_grant(BOB, "withholding-report"),
            operator_grant(BOB, "audit-integrity"),
        ],
    )
    _revoke_scope(client, "withholding-report")
    _revoke_scope(client, "audit-integrity")

    first = client.get(REVOKED_PATH, params={"limit": 1}, headers=auth_headers(ALICE))
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["grant_count"] == 1
    assert first_body["revoked_total"] == 2
    assert first_body["next_offset"] == 1
    assert first_body["truncated"] is True

    second = client.get(
        REVOKED_PATH,
        params={"limit": 1, "offset": first_body["next_offset"]},
        headers=auth_headers(ALICE),
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["grant_count"] == 1
    assert second_body["next_offset"] is None
    assert second_body["truncated"] is False

    scopes = {first_body["grants"][0]["scope"], second_body["grants"][0]["scope"]}
    assert scopes == {"withholding-report", "audit-integrity"}, "no row is skipped or repeated"


@pytest.mark.parametrize("limit", [0, -1])
def test_a_degenerate_limit_is_rejected_over_http(revoker_client: TestClient, limit: int) -> None:
    """``limit`` below one is refused, not silently turned into "everything".

    The route guards with ``ge=1`` so a mistyped limit cannot widen the read. A
    ``200`` here would let a caller with one bad number pull the whole history.
    """
    response = revoker_client.get(
        REVOKED_PATH, params={"limit": limit}, headers=auth_headers(ALICE)
    )
    assert response.status_code == 422, response.text


def test_a_limit_above_the_ceiling_is_rejected_not_clamped_over_http(
    revoker_client: TestClient,
) -> None:
    """An over-cap limit is refused so a caller cannot believe it read more than allowed."""
    response = revoker_client.get(
        REVOKED_PATH, params={"limit": 10_000}, headers=auth_headers(ALICE)
    )
    assert response.status_code == 422, response.text


def test_a_negative_offset_is_rejected_over_http(revoker_client: TestClient) -> None:
    response = revoker_client.get(REVOKED_PATH, params={"offset": -1}, headers=auth_headers(ALICE))
    assert response.status_code == 422, response.text


def test_a_non_operator_cannot_page_the_history(app: FastAPI) -> None:
    """Paging parameters do not open the surface to an outsider.

    A gated read answered ``404`` must stay ``404`` whatever the query string says;
    otherwise a non-operator could use the paging shape to probe for existence.
    """
    client = _client(
        app,
        ops_operator_user_ids=[operator_grant(BOB, "withholding-report")],
    )
    response = client.get(
        REVOKED_PATH, params={"limit": 1, "offset": 0}, headers=auth_headers(CAROL)
    )
    assert response.status_code == 404, response.text
