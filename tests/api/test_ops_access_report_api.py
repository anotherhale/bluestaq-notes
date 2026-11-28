"""The operator access report is operator-only at the HTTP boundary (R36).

``GetOperatorAccessReport`` is covered directly in
``tests/application/test_operator_access_report.py``. This file covers what that
cannot: that the route, the response schema and the container wiring agree, and
that the gate behaves the same way here as on every other ``/ops`` endpoint.

Two properties are load-bearing and asserted rather than assumed:

* **An empty allowlist admits nobody.** The default deployment grants this
  endpoint to no one, so an authenticated ordinary user cannot read who has been
  an operator simply by being logged in.
* **A non-operator sees 404, not 403**, so the endpoint stays indistinguishable
  from a path that does not exist.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, auth_headers, operator_grant

PATH = "/api/v1/ops/access-report"


def _client(app: FastAPI, **settings_updates: object) -> TestClient:
    """Rebuild the app from ``app``'s settings with overrides applied."""
    settings = app.state.container.settings.model_copy(update=settings_updates)
    from notes_service.bootstrap import app as app_module

    return TestClient(app_module.create_app(settings))


@pytest.fixture
def operator_client(app: FastAPI) -> TestClient:
    # Scoped to this endpoint's operation: the grant must not carry
    # over to another /ops view (NEX-044).
    return _client(app, ops_operator_user_ids=[operator_grant(ALICE, "operator-access-report")])


def test_operator_reads_the_access_report(operator_client: TestClient) -> None:
    response = operator_client.get(PATH, headers=auth_headers(ALICE))
    assert response.status_code == 200, response.text

    body = response.json()
    assert set(body) == {
        "entries",
        "limit",
        "log_name",
        "capacity",
        "truncated",
        "next_position",
    }

    # Reading this view is itself a granted operator read, so at least the
    # current call is recorded and names the operator.
    assert body["entries"], body
    assert body["entries"][0]["user_id"] == ALICE
    assert body["entries"][0]["operation"] == "operator-access-report"
    for entry in body["entries"]:
        assert set(entry) == {"user_id", "operation", "at", "position"}

    assert isinstance(body["limit"], int)
    assert body["limit"] >= 1
    assert isinstance(body["log_name"], str) and body["log_name"]
    assert isinstance(body["capacity"], int)
    assert isinstance(body["truncated"], bool)


def test_repeated_reads_are_each_recorded(operator_client: TestClient) -> None:
    """The trail grows with use, so an operator cannot inspect it silently."""
    first = operator_client.get(PATH, headers=auth_headers(ALICE)).json()
    second = operator_client.get(PATH, headers=auth_headers(ALICE)).json()

    assert len(second["entries"]) > len(first["entries"])
    assert second["entries"][0]["operation"] == "operator-access-report"


def test_empty_allowlist_admits_nobody(app: FastAPI) -> None:
    """The default configuration exposes this to no one, including a real user."""
    client = _client(app, ops_operator_user_ids=[])
    response = client.get(PATH, headers=auth_headers(ALICE))
    assert response.status_code == 404, response.text


def test_non_operator_gets_404_not_403(operator_client: TestClient) -> None:
    """A known user who is not on the list must not learn the route exists."""
    response = operator_client.get(PATH, headers=auth_headers(BOB))
    assert response.status_code == 404, response.text
    assert response.status_code != 403


def test_unauthenticated_caller_is_rejected(operator_client: TestClient) -> None:
    response = operator_client.get(PATH)
    assert response.status_code in {401, 404}, response.text
    assert response.status_code != 200


def test_a_limit_above_the_ceiling_is_rejected(operator_client: TestClient) -> None:
    """Out-of-range limits fail loudly instead of being silently clamped."""
    response = operator_client.get(PATH, headers=auth_headers(ALICE), params={"limit": 10_000})
    assert response.status_code == 422, response.text


def test_a_zero_limit_is_rejected(operator_client: TestClient) -> None:
    response = operator_client.get(PATH, headers=auth_headers(ALICE), params={"limit": 0})
    assert response.status_code == 422, response.text


def test_a_small_limit_returns_at_most_that_many(operator_client: TestClient) -> None:
    operator_client.get(PATH, headers=auth_headers(ALICE))
    response = operator_client.get(PATH, headers=auth_headers(ALICE), params={"limit": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["limit"] == 1
    assert len(body["entries"]) <= 1
