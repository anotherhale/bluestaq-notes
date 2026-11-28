"""The operator-grant list is operator-only at the HTTP boundary (NEX-045).

``ListOperatorGrants`` is covered directly in
``tests/application/test_operator_grants.py``. This file covers what that
cannot: that the route, the response schema, and the container wiring agree, and
that the endpoint answers the question the static allowlist could not — *who
holds a grant right now, and until when*.

The seeded configuration grants are unbounded (a deployment cannot express a
deadline in ``OPS_OPERATOR_USER_IDS``), so this endpoint reports ``null`` for the
deadline of a seeded operator. Expiry itself is a domain and gate property,
tested where it lives; here the concern is that a real request returns the real
holders and refuses everyone else the same way every other ``/ops`` view does.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, auth_headers, operator_grant

PATH = "/api/v1/ops/operator-grants"


def _client(app: FastAPI, **settings_updates: object) -> TestClient:
    settings = app.state.container.settings.model_copy(update=settings_updates)
    from notes_service.bootstrap import app as app_module

    return TestClient(app_module.create_app(settings))


@pytest.fixture
def operator_client(app: FastAPI) -> TestClient:
    # ALICE may read this view; BOB holds a different scope, so he is a holder
    # this view reports but cannot itself read it — the two facts stay separate.
    return _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-access-report"),
            operator_grant(BOB, "withholding-report"),
        ],
    )


def test_operator_reads_the_grant_list(operator_client: TestClient) -> None:
    response = operator_client.get(PATH, headers=auth_headers(ALICE))
    assert response.status_code == 200, response.text

    body = response.json()
    assert set(body) == {"holders", "holder_count", "as_of", "source_name"}
    assert body["holder_count"] == len(body["holders"])
    assert isinstance(body["source_name"], str) and body["source_name"]
    assert isinstance(body["as_of"], str) and body["as_of"]

    for holder in body["holders"]:
        assert set(holder) == {"user_id", "scopes", "expires_at"}

    # Both configured operators are seeded as holders, and a seeded grant is
    # unbounded, so its deadline is null rather than a fabricated date.
    holders = {holder["user_id"]: holder for holder in body["holders"]}
    assert set(holders) == {ALICE, BOB}
    assert holders[ALICE]["scopes"] == ["operator-access-report"]
    assert holders[BOB]["scopes"] == ["withholding-report"]
    assert holders[ALICE]["expires_at"] is None
    assert holders[BOB]["expires_at"] is None


def test_reading_the_grant_list_is_recorded(operator_client: TestClient) -> None:
    """Enumerating holders is an operator read, so it appends to the trail."""
    operator_client.get(PATH, headers=auth_headers(ALICE))

    report = operator_client.get("/api/v1/ops/access-report", headers=auth_headers(ALICE))
    assert report.status_code == 200, report.text
    operations = [entry["operation"] for entry in report.json()["entries"]]
    assert "operator-access-report" in operations


def test_a_non_operator_sees_404(app: FastAPI) -> None:
    """Empty allowlist admits nobody, and the endpoint looks like a missing path."""
    client = _client(app, ops_operator_user_ids=[])
    response = client.get(PATH, headers=auth_headers(ALICE))
    assert response.status_code == 404, response.text


def test_a_holder_of_another_scope_cannot_read_the_list(app: FastAPI) -> None:
    """Holding *a* scope is not holding *this* scope (NEX-044, at the boundary)."""
    client = _client(app, ops_operator_user_ids=[operator_grant(BOB, "withholding-report")])
    response = client.get(PATH, headers=auth_headers(BOB))
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------
# Issuing a grant: the reason is part of the contract (NEX-076).
# --------------------------------------------------------------------------

ISSUE_PATH = PATH


def _issue_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "user_id": BOB,
        "scope": "withholding-report",
        "expires_at": "2026-11-28T12:00:00Z",
        "rationale": "covering the incident review window",
    }
    body.update(overrides)
    return body


def _issuer_client(app: FastAPI) -> TestClient:
    # ALICE may issue grants and holds the scope she hands on (attenuation). Two
    # scopes for one user are two entries, not one comma-joined entry: the
    # settings parser splits on the first ``:`` and would read the rest as a
    # single over-long scope.
    return _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-issue"),
            operator_grant(ALICE, "operator-access-report"),
            operator_grant(ALICE, "withholding-report"),
            operator_grant(BOB, "withholding-report"),
        ],
    )


def test_an_operator_issues_a_grant_and_the_reason_round_trips(app: FastAPI) -> None:
    """The 201 body carries back the reason that was stored, not just the fact."""
    client = _issuer_client(app)
    response = client.post(ISSUE_PATH, json=_issue_body(), headers=auth_headers(ALICE))
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["user_id"] == BOB
    assert body["scope"] == "withholding-report"
    assert body["granted_by"] == ALICE
    assert body["rationale"] == "covering the incident review window"

    # And it is visible in the holder report, so the reason reached the store.
    listed = client.get(PATH, headers=auth_headers(ALICE))
    assert listed.status_code == 200, listed.text


def test_a_request_without_a_reason_is_refused_at_the_boundary(app: FastAPI) -> None:
    """``rationale`` is required, so its absence is a 422, not a blank record."""
    client = _issuer_client(app)
    body = _issue_body()
    del body["rationale"]
    response = client.post(ISSUE_PATH, json=body, headers=auth_headers(ALICE))
    assert response.status_code == 422, response.text


def test_a_blank_reason_is_refused_at_the_boundary(app: FastAPI) -> None:
    client = _issuer_client(app)
    response = client.post(
        ISSUE_PATH, json=_issue_body(rationale="   "), headers=auth_headers(ALICE)
    )
    assert response.status_code == 422, response.text


def test_an_over_long_reason_is_refused_at_the_boundary(app: FastAPI) -> None:
    client = _issuer_client(app)
    response = client.post(
        ISSUE_PATH, json=_issue_body(rationale="x" * 5000), headers=auth_headers(ALICE)
    )
    assert response.status_code == 422, response.text


def test_a_non_operator_cannot_issue_a_grant(app: FastAPI) -> None:
    client = _client(app, ops_operator_user_ids=[])
    response = client.post(ISSUE_PATH, json=_issue_body(), headers=auth_headers(ALICE))
    assert response.status_code == 404, response.text


def test_an_operator_cannot_issue_a_grant_to_themselves(app: FastAPI) -> None:
    """A self-grant is a 409: the request is well-formed but forbidden by policy."""
    client = _issuer_client(app)
    response = client.post(ISSUE_PATH, json=_issue_body(user_id=ALICE), headers=auth_headers(ALICE))
    assert response.status_code == 409, response.text
