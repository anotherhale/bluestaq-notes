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

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, CAROL, auth_headers, operator_grant

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
    assert set(body) == {
        "holders",
        "grants",
        "holder_count",
        "grant_count",
        "as_of",
        "source_name",
    }
    assert body["holder_count"] == len(body["holders"])
    assert body["grant_count"] == len(body["grants"])
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

    # The detail view carries the same grants un-folded, and a seeded grant is a
    # configuration grant: no human issuer, no per-row reason. That null pairing
    # is honest provenance, not a missing field (NEX-077). A live grant also
    # carries no revocation provenance — both fields null (NEX-080).
    for grant in body["grants"]:
        assert set(grant) == {
            "user_id",
            "scope",
            "granted_at",
            "granted_by",
            "expires_at",
            "rationale",
            "revoked_at",
            "revoked_by",
        }
        assert grant["granted_by"] is None
        assert grant["rationale"] is None
        assert grant["revoked_at"] is None
        assert grant["revoked_by"] is None


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

    # And it is readable from the list path, which is the point of NEX-077: the
    # reason was written by NEX-076 but no read path returned it, so a stored
    # justification an auditor could not read was one step short of useful.
    listed = client.get(PATH, headers=auth_headers(ALICE))
    assert listed.status_code == 200, listed.text

    detail = {(grant["user_id"], grant["scope"]): grant for grant in listed.json()["grants"]}
    issued = detail[(BOB, "withholding-report")]
    assert issued["granted_by"] == ALICE
    assert issued["rationale"] == "covering the incident review window"


def test_the_listed_reason_is_the_reason_that_was_stored(app: FastAPI) -> None:
    """Two grants for one holder keep their own issuer and reason, not a fold."""
    client = _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-issue"),
            operator_grant(ALICE, "operator-access-report"),
            operator_grant(ALICE, "withholding-report"),
            operator_grant(ALICE, "audit-integrity"),
            operator_grant(BOB, "withholding-report"),
        ],
    )
    first = client.post(
        ISSUE_PATH,
        json=_issue_body(scope="withholding-report", rationale="incident review window"),
        headers=auth_headers(ALICE),
    )
    assert first.status_code == 201, first.text
    second = client.post(
        ISSUE_PATH,
        json=_issue_body(scope="audit-integrity", rationale="quarterly access recertification"),
        headers=auth_headers(ALICE),
    )
    assert second.status_code == 201, second.text

    listed = client.get(PATH, headers=auth_headers(ALICE))
    assert listed.status_code == 200, listed.text
    body = listed.json()

    # One holder, two grants: the fold must report BOB once, and the detail must
    # keep both reasons distinct rather than collapsing them to one.
    reasons = {
        grant["scope"]: grant["rationale"] for grant in body["grants"] if grant["user_id"] == BOB
    }
    assert reasons == {
        "withholding-report": "incident review window",
        "audit-integrity": "quarterly access recertification",
    }
    assert body["grant_count"] > body["holder_count"]


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


def test_a_deadline_beyond_the_issuers_own_is_a_409(app: FastAPI) -> None:
    """Temporal attenuation holds through the route, the store and the gate (NEX-084).

    The use case is covered directly in ``tests/application/test_issue_operator_grant.py``.
    This covers what that cannot: that the ceiling survives the round trip through
    the store and the gate, rather than only existing on the object the use case
    returned.

    BOB is *not* given ``withholding-report`` by configuration, because a seeded
    grant is unbounded and would give him no ceiling to exceed. His only authority
    for that scope is the grant ALICE issues below, which is short. His
    ``operator-grant-issue`` config grant lets him reach the endpoint, and being a
    different scope it must not widen that ceiling.

    409 rather than 422: the request is well formed and the scope is legal; policy
    refuses it. 403 would be wrong too — BOB reaches this resource, so this is the
    known-but-insufficient answer, not an existence-hiding 404.
    """
    now = datetime.now(UTC)
    client = _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "operator-grant-issue"),
            operator_grant(ALICE, "withholding-report"),
            operator_grant(BOB, "operator-grant-issue"),
        ],
    )
    short = now + timedelta(minutes=90)
    issued = client.post(
        ISSUE_PATH,
        json=_issue_body(user_id=BOB, expires_at=short.isoformat()),
        headers=auth_headers(ALICE),
    )
    assert issued.status_code == 201, issued.text

    refused = client.post(
        ISSUE_PATH,
        json=_issue_body(user_id=CAROL, expires_at=(now + timedelta(days=30)).isoformat()),
        headers=auth_headers(BOB),
    )
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["error"]["code"] == "conflict"
    # The refusal says *why* in terms an operator can act on: they may still issue,
    # just not past their own deadline. It names the scope, not the other user.
    assert "may not issue a grant that outlives" in body["error"]["message"]

    # And the rule is a ceiling, not a ban: BOB may still issue inside it.
    allowed = client.post(
        ISSUE_PATH,
        json=_issue_body(user_id=CAROL, expires_at=(now + timedelta(minutes=45)).isoformat()),
        headers=auth_headers(BOB),
    )
    assert allowed.status_code == 201, allowed.text
