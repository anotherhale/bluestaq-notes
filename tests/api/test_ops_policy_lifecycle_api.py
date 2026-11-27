"""The policy lifecycle over HTTP is operator-only and attributes the publisher (NEX-049).

The use cases are covered directly in ``tests/application/test_policy_lifecycle.py``.
This file covers what that cannot: route/schema/container agreement, and the one
security rule that lives only at the boundary — the publisher is stamped from the
authenticated principal, so a request body cannot attribute a policy in force to
someone else.

Two properties are load-bearing:

* **An empty operator allowlist admits nobody.** The default deployment grants
  these routes to no one, so an authenticated ordinary user must not be able to
  put a policy in force merely by being logged in.
* **A non-operator sees 404, not 403.** A 403 would confirm that a policy
  lifecycle surface exists at this path.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, auth_headers, operator_grant

BASE = "/api/v1/ops/policies"

DRAFT = {
    "rules": [
        {
            "rule_id": "allow-low-risk",
            "effect": "allow",
            "when": {
                "kind": "comparison",
                "attribute": {"name": "risk", "type": "string"},
                "operator": "==",
                "value": "low",
            },
        }
    ],
    "rationale": "permit low-risk actions",
}


def _client(app: FastAPI, **settings_updates: object) -> TestClient:
    settings = app.state.container.settings.model_copy(update=settings_updates)
    from notes_service.bootstrap import app as app_module

    return TestClient(app_module.create_app(settings))


@pytest.fixture
def operator_client(app: FastAPI) -> TestClient:
    # Every lifecycle operation is scoped separately, so the grant must name
    # each one as its own entry: a wildcard here would hide a missing scope
    # check. ``model_copy(update=...)`` bypasses the settings validator that
    # splits a comma-joined string, so each grant is a separate list item.
    return _client(
        app,
        ops_operator_user_ids=[
            operator_grant(ALICE, "policy-draft"),
            operator_grant(ALICE, "policy-publish"),
            operator_grant(ALICE, "policy-rollback"),
            operator_grant(ALICE, "policy-history"),
        ],
    )


def test_operator_drafts_then_publishes(operator_client: TestClient) -> None:
    drafted = operator_client.post(
        f"{BASE}/risk-gate/versions", json=DRAFT, headers=auth_headers(ALICE)
    )
    assert drafted.status_code == 201, drafted.text
    body = drafted.json()
    assert body["policy_id"] == "risk-gate"
    assert body["version"] == 1
    assert body["status"] == "draft"
    # A draft is not in force, so it has no publisher.
    assert body["published_by"] is None
    assert body["rules"][0]["rule_id"] == "allow-low-risk"
    assert body["rules"][0]["effect"] == "allow"

    published = operator_client.post(
        f"{BASE}/risk-gate/publish",
        json={"version": 1},
        headers=auth_headers(ALICE),
    )
    assert published.status_code == 200, published.text
    in_force = published.json()
    assert in_force["status"] == "published"
    # The publisher is the caller, stamped at the boundary.
    assert in_force["published_by"] == f"user-{ALICE}"


def test_publisher_cannot_be_chosen_by_the_request(operator_client: TestClient) -> None:
    """``published_by`` is not a request field; ``extra="forbid"`` rejects it."""
    operator_client.post(f"{BASE}/p2/versions", json=DRAFT, headers=auth_headers(ALICE))
    response = operator_client.post(
        f"{BASE}/p2/publish",
        json={"version": 1, "published_by": str(BOB)},
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 422, response.text


def test_history_reports_the_version_in_force(operator_client: TestClient) -> None:
    operator_client.post(f"{BASE}/p3/versions", json=DRAFT, headers=auth_headers(ALICE))
    operator_client.post(f"{BASE}/p3/publish", json={"version": 1}, headers=auth_headers(ALICE))

    response = operator_client.get(f"{BASE}/p3/versions", headers=auth_headers(ALICE))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["policy_id"] == "p3"
    assert [v["version"] for v in body["versions"]] == [1]
    assert body["published_version"] == 1


def test_rollback_keeps_the_history(operator_client: TestClient) -> None:
    operator_client.post(f"{BASE}/p4/versions", json=DRAFT, headers=auth_headers(ALICE))
    operator_client.post(f"{BASE}/p4/publish", json={"version": 1}, headers=auth_headers(ALICE))

    rolled_back = operator_client.post(
        f"{BASE}/p4/rollback",
        json={"to_version": 1, "rationale": "restore"},
        headers=auth_headers(ALICE),
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["version"] == 2

    history = operator_client.get(f"{BASE}/p4/versions", headers=auth_headers(ALICE)).json()
    # Nothing is deleted: the rollback is itself a version.
    assert [v["version"] for v in history["versions"]] == [1, 2]


def test_rolling_back_to_a_draft_is_a_conflict(operator_client: TestClient) -> None:
    operator_client.post(f"{BASE}/p5/versions", json=DRAFT, headers=auth_headers(ALICE))
    response = operator_client.post(
        f"{BASE}/p5/rollback",
        json={"to_version": 1, "rationale": "too early"},
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 409, response.text


def test_publishing_an_unknown_version_is_not_found(operator_client: TestClient) -> None:
    response = operator_client.post(
        f"{BASE}/nope/publish", json={"version": 7}, headers=auth_headers(ALICE)
    )
    assert response.status_code == 404, response.text


def test_a_policy_needs_at_least_one_rule(operator_client: TestClient) -> None:
    response = operator_client.post(
        f"{BASE}/empty/versions",
        json={"rules": [], "rationale": "x"},
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 422, response.text


def test_an_unknown_effect_is_refused(operator_client: TestClient) -> None:
    response = operator_client.post(
        f"{BASE}/bad-effect/versions",
        json={
            "rules": [{"rule_id": "r", "effect": "maybe", "when": None}],
            "rationale": "x",
        },
        headers=auth_headers(ALICE),
    )
    assert response.status_code == 422, response.text


def test_a_non_operator_sees_404_everywhere(app: FastAPI) -> None:
    """No operator allowlist: an ordinary authenticated user is not an operator."""
    client = _client(app)
    assert (
        client.post(f"{BASE}/p6/versions", json=DRAFT, headers=auth_headers(BOB)).status_code == 404
    )
    assert (
        client.post(
            f"{BASE}/p6/publish", json={"version": 1}, headers=auth_headers(BOB)
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{BASE}/p6/rollback",
            json={"to_version": 1, "rationale": "x"},
            headers=auth_headers(BOB),
        ).status_code
        == 404
    )
    assert client.get(f"{BASE}/p6/versions", headers=auth_headers(BOB)).status_code == 404


def test_a_grant_for_one_operation_does_not_cover_another(app: FastAPI) -> None:
    """Drafting is not publishing: scopes are per operation, not per endpoint family."""
    client = _client(app, ops_operator_user_ids=[operator_grant(ALICE, "policy-draft")])
    assert (
        client.post(f"{BASE}/p7/versions", json=DRAFT, headers=auth_headers(ALICE)).status_code
        == 201
    )
    assert (
        client.post(
            f"{BASE}/p7/publish", json={"version": 1}, headers=auth_headers(ALICE)
        ).status_code
        == 404
    )


def test_an_unauthenticated_caller_is_rejected(operator_client: TestClient) -> None:
    assert operator_client.post(f"{BASE}/p8/versions", json=DRAFT).status_code == 401
